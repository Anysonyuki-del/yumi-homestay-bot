package icu.akros.yumi

import android.graphics.Color
import android.os.Bundle
import android.net.Uri
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.WebView
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AlertDialog
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import java.io.File

class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    // Android 15 起，targetSdk 35 及以上的应用一律强制全屏绘制：
    // 不调用 enableEdgeToEdge 也会延伸到状态栏和导航栏下面，
    // 所以不能靠去掉这一行来避让，必须在下面自行处理 inset。
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    // 必须在 Activity 进入 STARTED 之前注册；onWebViewCreate 触发得更晚，
    // 放在那里注册会抛异常，所以这里先建好启动器。
    saveLauncher = registerForActivityResult(ImageSaver.CreateImageDocument()) { uri ->
      finishImageSave(uri)
    }
  }

  /**
   * WebView 创建后按系统栏与输入法尺寸把视图矩形收进安全区。
   *
   * 这里用 **margin 而不是 padding**，是真机实测定下来的：
   * 先前对 WebView `setPadding(上 140)` 后，诊断页读到的 innerHeight 仍是 800 CSS px
   * （= 2800 物理 px ÷ 3.5，整屏高度），visualViewport.offsetTop 为 0，
   * 说明 padding 并没有缩小网页视口，sticky 顶栏照样画在状态栏下面。
   * 改成 margin 后收缩的是 View 矩形本身，网页视口随之变小（实测 760）。
   *
   * 安全边界取 systemBars 与 displayCutout 各边的较大值：刘海屏横屏时
   * cutout 可能超出 systemBars。全部来自系统实际派发的 inset，不写死状态栏常量。
   * 每次直接赋绝对值而不是累加，键盘反复开合、反复旋转都不会累积空白。
   */
  override fun onWebViewCreate(webView: WebView) {
    ViewCompat.setOnApplyWindowInsetsListener(webView) { view, insets ->
      val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
      val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
      val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
      val imeVisible = insets.isVisible(WindowInsetsCompat.Type.ime())

      val left = maxOf(bars.left, cutout.left)
      val top = maxOf(bars.top, cutout.top)
      val right = maxOf(bars.right, cutout.right)
      // 键盘弹出时底部避让交给 IME inset；三者取大值，避免同时扣两次。
      val bottom = maxOf(maxOf(bars.bottom, cutout.bottom), ime.bottom)

      val params = view.layoutParams as? ViewGroup.MarginLayoutParams
      if (params != null) {
        params.setMargins(left, top, right, bottom)
        view.layoutParams = params
        view.setPadding(0, 0, 0, 0)
      } else {
        // 父容器不支持 margin 时退回 padding：至少不会比不做更差，
        // 但诊断里会标明走了哪条路径，不把兜底当成已验证的正确行为。
        view.setPadding(left, top, right, bottom)
      }
      errorOverlay?.let { applySafeArea(it, left, top, right, bottom) }

      lastSafeArea = SafeAreaMetrics(
        left = left,
        top = top,
        right = right,
        bottom = bottom,
        barsTop = bars.top,
        barsBottom = bars.bottom,
        cutoutTop = cutout.top,
        cutoutBottom = cutout.bottom,
        imeBottom = ime.bottom,
        imeVisible = imeVisible,
        usedMargin = params != null,
      )
      // 诊断采集只在 diagnostics 构建里有实现；其余模式这里是空操作，
      // 正式包中不存在可执行的测量入口。
      DiagnosticsReporter.onSafeArea(this, view as WebView, lastSafeArea!!)

      // 返回原 insets 而不是 CONSUMED：让系统继续把 inset 分发给其他视图。
      insets
    }
    ViewCompat.requestApplyInsets(webView)

    installSafeClient(webView)
    installBackHandling(webView)
    installImageSaving(webView)

    DiagnosticsReporter.install(this, webView)
  }

  // ---- 连接错误：原生界面，不导航到本地页 ----

  private var errorOverlay: View? = null

  /**
   * 把 Tauri 的 WebViewClient 包一层，补上禁止表单重提交与主框架错误处理。
   *
   * 错误界面刻意做成原生覆盖层，而不是导航到打包的 HTML：
   * 导航到本地资源就得在导航策略上为本地协议开一个口子，而 Spec 要求
   * 「来自远程页的任意本地协议导航一律拒绝」。原生层画界面完全不产生导航，
   * 重试则是一次指向固定工作台入口的普通 GET，照常受策略检查。
   */
  private fun installSafeClient(webView: WebView) {
    val original = webView.webViewClient
    webView.webViewClient = SafeWebViewClient(original) { view, _, _ ->
      showErrorOverlay(view)
      true
    }
  }

  /** 按安全区给覆盖层留边，使其与 WebView 对齐而不是压在系统栏下。 */
  private fun applySafeArea(view: View, left: Int, top: Int, right: Int, bottom: Int) {
    (view.layoutParams as? ViewGroup.MarginLayoutParams)?.let {
      it.setMargins(left, top, right, bottom)
      view.layoutParams = it
    }
  }

  private fun dp(value: Int): Int =
    TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics)
      .toInt()

  /** 构建并显示连接错误界面；重复失败只显示一次。 */
  private fun showErrorOverlay(webView: WebView) {
    if (errorOverlay != null) {
      errorOverlay?.visibility = View.VISIBLE
      return
    }
    val parent = webView.parent as? ViewGroup ?: return

    val panel = LinearLayout(this).apply {
      orientation = LinearLayout.VERTICAL
      gravity = Gravity.CENTER_VERTICAL
      setBackgroundColor(Color.WHITE)
      setPadding(dp(24), dp(24), dp(24), dp(24))
      isClickable = true // 吃掉点击，避免穿透到下面的 WebView
    }
    panel.addView(TextView(this).apply {
      text = getString(R.string.net_error_title)
      setTextColor(Color.parseColor("#1c1c1e"))
      setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
      setPadding(0, 0, 0, dp(12))
    })
    panel.addView(TextView(this).apply {
      text = getString(R.string.net_error_body)
      setTextColor(Color.parseColor("#5a5a5e"))
      setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
      setPadding(0, 0, 0, dp(20))
    })
    panel.addView(Button(this).apply {
      text = getString(R.string.net_error_retry)
      setOnClickListener {
        hideErrorOverlay()
        // 只 GET 固定入口，不 reload、不 goBack，因此永远不会重放上一次提交。
        // 地址取自 strings.xml，与 Rust 的 WORKSTATION_URL 由单元测试锁定一致。
        webView.loadUrl(workstationUrl())
      }
    })
    panel.addView(TextView(this).apply {
      text = getString(R.string.net_error_note)
      setTextColor(Color.parseColor("#5a5a5e"))
      setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
      setPadding(0, dp(16), 0, 0)
    })

    val lp = ViewGroup.MarginLayoutParams(
      ViewGroup.LayoutParams.MATCH_PARENT,
      ViewGroup.LayoutParams.MATCH_PARENT,
    )
    parent.addView(panel, lp)
    errorOverlay = panel
    lastSafeArea?.let { applySafeArea(panel, it.left, it.top, it.right, it.bottom) }
  }

  private fun hideErrorOverlay() {
    errorOverlay?.visibility = View.GONE
  }

  // ---- 返回键 ----

  /**
   * 按 Spec F-03 的顺序接管返回：键盘 → 错误界面 → 可回退历史 → 根页面确认退出。
   *
   * `TauriActivity` 已把 Wry 默认的无条件 `goBack()` 关掉（handleBackNavigation = false），
   * 所以这里是唯一的返回处理方，不会和框架打架。
   *
   * 网页内的抽屉/弹层由页面自己关闭，原生层不去猜它开没开——
   * 原生看不到 DOM，猜错会让返回键在有弹层时失灵。
   *
   * **不直接 goBack**：原生层看不到历史条目的 HTTP 方法，
   * `WebBackForwardList` 只给 URL，无法证明上一页是安全的 GET。
   * `onFormResubmission` 只保证不重发网络请求，不保证回退后页面状态正确，
   * 也不保证当前页未保存的输入不丢。因此按 R2 允许的最小方案：
   * 原生确认离开并提示未保存内容会丢失，确认后 GET 当前构建的工作台入口。
   * 首版不为此构建通用的历史请求追踪系统。
   */
  private fun installBackHandling(webView: WebView) {
    onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
      override fun handleOnBackPressed() {
        val insets = ViewCompat.getRootWindowInsets(webView)
        if (insets?.isVisible(WindowInsetsCompat.Type.ime()) == true) {
          WindowCompat.getInsetsController(window, webView)
            .hide(WindowInsetsCompat.Type.ime())
          return
        }
        if (errorOverlay?.visibility == View.VISIBLE) {
          hideErrorOverlay()
          webView.loadUrl(workstationUrl())
          return
        }
        if (webView.canGoBack()) {
          confirmLeavePage(webView)
          return
        }
        confirmExit()
      }
    })
  }

  /**
   * 可回退时的离开确认。
   *
   * 确认后不调 goBack，而是 GET 固定工作台入口：那是唯一能证明安全的目标。
   */
  private fun confirmLeavePage(webView: WebView) {
    AlertDialog.Builder(this)
      .setTitle(R.string.back_confirm_title)
      .setMessage(R.string.back_confirm_body)
      .setNegativeButton(R.string.back_confirm_cancel, null)
      .setPositiveButton(R.string.back_confirm_ok) { _, _ ->
        webView.loadUrl(workstationUrl())
      }
      .show()
  }

  /** 根页面再次返回时确认退出，并提醒关闭软件不等于退出登录。 */
  private fun confirmExit() {
    AlertDialog.Builder(this)
      .setTitle(R.string.exit_confirm_title)
      .setMessage(R.string.exit_confirm_body)
      .setNegativeButton(R.string.exit_confirm_cancel, null)
      .setPositiveButton(R.string.exit_confirm_ok) { _, _ -> finish() }
      .show()
  }

  // ---- 受保护图片保存（F-04） ----

  private var lastSafeArea: SafeAreaMetrics? = null
  private lateinit var saveLauncher: ActivityResultLauncher<Pair<String, String>>

  /** 同一时刻只允许一个在途保存，回调按操作 ID 匹配，避免两次下载交错串单。 */
  private val saveSession = SaveSession()

  /**
   * 长按图片时提供保存入口。
   *
   * wry 没有占用长按与命中测试，这里是空闲的官方扩展点，不需要包装任何框架对象。
   * 只对本次长按命中的那一张图提供操作：不扫描页面、不批量抓取。
   */
  private fun installImageSaving(webView: WebView) {
    webView.setOnLongClickListener {
      val hit = webView.hitTestResult
      val url = hit.extra
      val isImage = hit.type == WebView.HitTestResult.IMAGE_TYPE ||
        hit.type == WebView.HitTestResult.SRC_IMAGE_ANCHOR_TYPE
      if (isImage && ImageSaver.isSaveable(url, workstationUrl())) {
        confirmSaveImage(url!!)
        true
      } else {
        false
      }
    }
  }

  /** 本次构建的工作台入口，与 Rust 的 WORKSTATION_URL 同源（由 build.rs 生成）。 */
  private fun workstationUrl(): String = getString(R.string.workstation_url)

  /** 先确认再下载：保存必须由用户本次明确触发。 */
  private fun confirmSaveImage(url: String) {
    if (saveSession.busy) {
      // 不排队：静默排队会让第二张图在很久以后突然弹出选择器，用户已经忘了它。
      Toast.makeText(this, R.string.save_busy, Toast.LENGTH_SHORT).show()
      return
    }
    AlertDialog.Builder(this)
      .setTitle(R.string.save_image_title)
      .setMessage(R.string.save_image_body)
      .setNegativeButton(R.string.save_image_cancel, null)
      .setPositiveButton(R.string.save_image_ok) { _, _ -> startImageDownload(url) }
      .show()
  }

  /**
   * 后台下载并校验，成功后才弹系统文档创建流程。
   *
   * 顺序不能反：先让用户选位置再下载，一旦失败就会在用户选中的位置留下
   * 空文件或一份登录页 HTML，那正是 Spec 禁止的假成功。
   */
  private fun startImageDownload(url: String) {
    val operation = saveSession.begin(url)
    if (operation == null) {
      Toast.makeText(this, R.string.save_busy, Toast.LENGTH_SHORT).show()
      return
    }
    Toast.makeText(this, R.string.save_image_working, Toast.LENGTH_SHORT).show()
    val workstation = workstationUrl()
    Thread {
      val result = ImageSaver.download(this, url, workstation)
      runOnUiThread {
        // Activity 已销毁或该操作已被作废时，这次回调不得再影响界面，
        // 只清理它自己下载的临时文件。
        if (isFinishing || isDestroyed || !saveSession.isCurrent(operation.id)) {
          (result as? ImageSaver.Result.Ready)?.temp?.delete()
          return@runOnUiThread
        }
        when (result) {
          is ImageSaver.Result.Ready -> {
            saveSession.operation(operation.id)?.temp = result.temp
            saveLauncher.launch(result.mimeType to result.suggestedName)
          }
          is ImageSaver.Result.Failed -> {
            saveSession.finish(operation.id)?.delete()
            Toast.makeText(this, result.message, Toast.LENGTH_LONG).show()
          }
        }
      }
    }.start()
  }

  /** 用户选好位置后写入；取消或写入失败都不报成功。 */
  private fun finishImageSave(target: Uri?) {
    val temp = saveSession.finishCurrent() ?: run {
      // 选择器回传但已无有效操作：不写入，也不猜测该 URI 的归属。
      if (target != null) Toast.makeText(this, R.string.save_stale, Toast.LENGTH_LONG).show()
      return
    }
    if (target == null) {
      // 用户取消：只删掉自己创建的临时文件，不动任何既有文件。
      temp.delete()
      Toast.makeText(this, R.string.save_image_cancelled, Toast.LENGTH_SHORT).show()
      return
    }
    Thread {
      val outcome = ImageSaver.commit(this, temp, target)
      runOnUiThread {
        if (isFinishing || isDestroyed) return@runOnUiThread
        val message = when (outcome) {
          ImageSaver.CommitResult.OK -> R.string.save_image_done
          ImageSaver.CommitResult.FAILED_CLEAN -> R.string.save_image_write_failed
          ImageSaver.CommitResult.FAILED_PARTIAL -> R.string.save_partial
        }
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
      }
    }.start()
  }

  override fun onDestroy() {
    // 销毁时作废在途保存：此后旧回调 ID 不再匹配，不会再弹窗或写文件。
    saveSession.invalidate()?.delete()
    super.onDestroy()
  }
}
