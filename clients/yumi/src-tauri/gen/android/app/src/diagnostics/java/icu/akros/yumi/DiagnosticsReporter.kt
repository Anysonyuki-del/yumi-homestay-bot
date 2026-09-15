package icu.akros.yumi

import android.app.Activity
import android.graphics.Rect
import android.os.Build
import android.provider.Settings
import android.view.View
import android.webkit.WebView
import androidx.webkit.WebViewCompat
import org.json.JSONObject

/**
 * 诊断构建专用：采集设备与几何数值并注入本地测量页。
 *
 * 只存在于 diagnostics 构建（由 identity.gradle 按构建模式选择源码集），
 * 因此 production 与 isolated-test 包里不含本文件的任何可执行代码。
 *
 * 注入是单向的原生 → 网页推送：不注册任何 invoke 命令，网页无法反向调用原生，
 * capabilities 仍为空。内容只有设备与几何数值，不含业务数据、Cookie 或 URL。
 */
object DiagnosticsReporter {
  const val ENABLED = true

  private var latest: SafeAreaMetrics? = null
  private var injected = false

  /** 页面加载完成的时机拿不到（RustWebViewClient 位于每次重生成的 generated/），
   *  因此在最初几秒轮询补注入。 */
  fun install(activity: Activity, webView: WebView) {
    scheduleRetry(activity, webView, remaining = 20)
  }

  fun onSafeArea(activity: Activity, webView: WebView, metrics: SafeAreaMetrics) {
    latest = metrics
    inject(activity, webView)
  }

  private fun scheduleRetry(activity: Activity, webView: WebView, remaining: Int) {
    if (injected || remaining <= 0) return
    webView.postDelayed({
      inject(activity, webView)
      scheduleRetry(activity, webView, remaining - 1)
    }, 300L)
  }

  private fun inject(activity: Activity, webView: WebView) {
    val metrics = latest ?: return
    if (!DiagnosticsGate.isDiagnosticsPage(webView.url)) return

    val payload = collect(activity, webView, metrics)
    // JSONObject.toString() 已完成转义，再套一层 JSON.parse 避免脚本注入歧义。
    val json = JSONObject.quote(payload.toString())
    webView.evaluateJavascript(
      "window.__yumiNative = JSON.parse($json);" +
        "if (window.__yumiOnNative) { window.__yumiOnNative(); }",
      null,
    )
    injected = true
  }

  private fun collect(activity: Activity, webView: WebView, m: SafeAreaMetrics): JSONObject {
    val display = activity.resources.displayMetrics
    val config = activity.resources.configuration
    val location = IntArray(2)
    webView.getLocationOnScreen(location)
    val visibleFrame = Rect().also { webView.getWindowVisibleDisplayFrame(it) }

    // 0=三键, 1=两键, 2=手势。读不到时标记未知，不猜测。
    val navMode = try {
      Settings.Secure.getInt(activity.contentResolver, "navigation_mode")
    } catch (_: Throwable) {
      -1
    }

    return JSONObject().apply {
      put("构建模式", activity.getString(R.string.build_mode))
      put("机型", "${Build.MANUFACTURER} ${Build.MODEL}")
      put("Android / API", "${Build.VERSION.RELEASE} / ${Build.VERSION.SDK_INT}")
      put(
        "WebView 版本",
        WebViewCompat.getCurrentWebViewPackage(activity)?.versionName ?: "未知",
      )
      put("density / densityDpi", "${display.density} / ${display.densityDpi}")
      put("系统 fontScale", config.fontScale)
      put("屏幕方向", if (config.orientation == 1) "竖屏" else "横屏")
      put(
        "导航方式",
        when (navMode) {
          0 -> "三键"
          1 -> "两键"
          2 -> "手势"
          else -> "未知"
        },
      )
      put("WebView 屏幕坐标", "${location[0]}, ${location[1]}")
      put("WebView 宽 × 高(px)", "${webView.width} × ${webView.height}")
      put("避让方式", if (m.usedMargin) "margin（视图矩形收缩）" else "padding（兜底，未验证）")
      put("已施加边距(px)", "上 ${m.top} / 下 ${m.bottom} / 左 ${m.left} / 右 ${m.right}")
      put("父容器", (webView.parent as? View)?.javaClass?.simpleName ?: "未知")
      put("WebViewClient", webView.webViewClient.javaClass.simpleName)
      put("可回退历史", webView.canGoBack())
      put("systemBars inset", "上 ${m.barsTop} / 下 ${m.barsBottom}")
      put("displayCutout inset", "上 ${m.cutoutTop} / 下 ${m.cutoutBottom}")
      put("IME inset / 可见", "${m.imeBottom} / ${m.imeVisible}")
      put(
        "可见窗口边界",
        "${visibleFrame.left},${visibleFrame.top} - ${visibleFrame.right},${visibleFrame.bottom}",
      )
    }
  }
}
