package icu.akros.yumi

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.DocumentsContract
import android.webkit.CookieManager
import androidx.activity.result.contract.ActivityResultContract
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 受保护图片的原生保存实现。
 *
 * 这不是通用下载器：只接受与工作台同源的 HTTPS GET，逐跳限制同源，
 * 并且必须先确认响应真的是图片，才会去创建用户选择的文件（Spec F-04）。
 *
 * 关键顺序：**先下载到应用私有临时文件并校验，再让用户选保存位置**。
 * 反过来做的话，一旦下载失败或响应其实是登录页 HTML，
 * 用户选中的位置上就已经躺着一个空文件或一份 HTML，
 * 那正是 Spec 禁止的「把登录 HTML 存成图片并报告成功」。
 */
object ImageSaver {

  /** 允许保存的图片类型。不在此列的一律拒绝，不按扩展名猜。 */
  private val SUPPORTED = mapOf(
    "image/png" to "png",
    "image/jpeg" to "jpg",
    "image/webp" to "webp",
    "image/gif" to "gif",
    "image/heic" to "heic",
  )

  /** 逐跳重定向上限，防止被无限重定向拖住。 */
  private const val MAX_HOPS = 5

  sealed interface Result {
    /** 下载与校验都通过，临时文件已就绪，等待用户选择保存位置。 */
    data class Ready(val temp: File, val mimeType: String, val suggestedName: String) : Result

    /** 任何失败。message 面向用户，不包含 URL、Cookie 或响应正文。 */
    data class Failed(val message: String) : Result
  }

  /**
   * 判断该地址是否允许走保存流程。
   *
   * 与导航策略同一条判据：scheme、主机、有效端口三项都要与工作台入口一致。
   * 外域图片不提供保存入口，避免把这里变成通用下载器。
   */
  fun isSaveable(rawUrl: String?, workstationUrl: String): Boolean {
    val raw = rawUrl ?: return false
    val target = runCatching { URL(raw) }.getOrNull() ?: return false
    val origin = runCatching { URL(workstationUrl) }.getOrNull() ?: return false
    // 与 Rust 导航策略保持同一判据：带用户信息的 URL 一律拒绝。
    // java.net.URL 会把 userInfo 解析掉而 host 仍是真实主机，
    // 因此 https://akros.icu@evil.example 本就不会同源；
    // 但 https://user@akros.icu/x 会被判为同源——若不显式拒绝，
    // 两侧策略就在这里分叉：Rust 拦、Kotlin 放。
    if (!target.userInfo.isNullOrEmpty()) return false
    return sameOrigin(target, origin)
  }

  private fun sameOrigin(a: URL, b: URL): Boolean =
    a.protocol.equals(b.protocol, ignoreCase = true) &&
      a.host.equals(b.host, ignoreCase = true) &&
      effectivePort(a) == effectivePort(b)

  private fun effectivePort(url: URL): Int =
    if (url.port != -1) url.port else url.defaultPort

  /**
   * 下载并校验，成功时返回已落盘的临时文件。
   *
   * 必须在后台线程调用。Cookie 只从平台 CookieManager 取，
   * 不经过网页 JavaScript，也不会发往非同源地址。
   */
  fun download(context: Context, rawUrl: String, workstationUrl: String): Result {
    if (!isSaveable(rawUrl, workstationUrl)) {
      return Result.Failed("这张图片不是工作台的附件，未提供保存。")
    }

    var current = URL(rawUrl)
    val origin = URL(workstationUrl)
    var hops = 0
    var connection: HttpURLConnection? = null

    try {
      while (true) {
        if (hops++ > MAX_HOPS) {
          return Result.Failed("服务器重定向次数过多，未保存。")
        }
        // 逐跳都要重新确认同源：跨到外部地址就停，且绝不把 Cookie 带过去。
        if (!sameOrigin(current, origin)) {
          return Result.Failed("图片地址跳转到了站外，出于安全未保存。")
        }

        connection = (current.openConnection() as HttpURLConnection).apply {
          requestMethod = "GET"
          instanceFollowRedirects = false // 自己逐跳判定，不交给系统自动跟随
          connectTimeout = 15000
          readTimeout = 30000
          CookieManager.getInstance().getCookie(current.toString())?.let {
            setRequestProperty("Cookie", it)
          }
        }

        val code = connection.responseCode
        if (code in 300..399) {
          val location = connection.getHeaderField("Location")
            ?: return Result.Failed("服务器重定向缺少目标地址，未保存。")
          current = URL(current, location)
          connection.disconnect()
          continue
        }
        if (code == 401 || code == 403) {
          return Result.Failed("登录状态已失效或没有权限，请重新登录后再试。")
        }
        if (code !in 200..299) {
          return Result.Failed("服务器返回 $code，未保存。")
        }

        // 会话失效时后台会返回 200 的登录页 HTML。只按声明类型判定，
        // 不按 URL 猜、也不把非图片正文写进用户文件。
        val declared = connection.contentType?.substringBefore(';')?.trim()?.lowercase()
        val extension = SUPPORTED[declared]
        if (declared == null || extension == null) {
          return Result.Failed(
            if (declared?.startsWith("text/") == true) {
              "登录状态可能已失效，服务器返回的不是图片，未保存。"
            } else {
              "这个文件不是受支持的图片类型，未保存。"
            }
          )
        }

        return streamToTemp(
          context,
          connection.inputStream,
          declared,
          extension,
          connection.contentLengthLong,
        )
      }
    } catch (_: Throwable) {
      return Result.Failed("下载中断，未保存。")
    } finally {
      connection?.disconnect()
    }
  }

  /**
   * 单张附件上限。
   *
   * 取服务端**允许配置的最大值**而不是默认值：
   * `config.py::private_upload_max_bytes` 默认 10 MiB，但可配到 25 MiB
   * （字段约束 `le=25 * 1024 * 1024`）。客户端若按默认值设限，
   * 部署方调高配置后，服务端已接受的合法附件在这里反而存不下。
   * 这个值只用于防止无界写入，不承担业务限额职责。
   */
  private const val MAX_BYTES = 25L * 1024 * 1024

  /**
   * 流式写入应用私有临时文件，并核对长度与真实图片内容。
   *
   * 只判非空是不够的：会话失效时后台返回的是 200 的登录页 HTML，
   * 截断的响应也可能留下一个「非空但不是完整图片」的文件。
   */
  private fun streamToTemp(
    context: Context,
    input: InputStream,
    mime: String,
    extension: String,
    declaredLength: Long,
  ): Result {
    val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
    // 中性文件名：不沿用 URL 末段（那是私有附件标识），也不含任何客户信息。
    val name = "YuMi_图片_$stamp.$extension"
    val temp = File.createTempFile("yumi_img_", ".$extension", context.cacheDir)

    var written = 0L
    try {
      input.use { source ->
        temp.outputStream().use { sink ->
          val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
          while (true) {
            val read = source.read(buffer)
            if (read < 0) break
            written += read
            if (written > MAX_BYTES) {
              temp.delete()
              return Result.Failed("图片超过单张附件上限，未保存。")
            }
            sink.write(buffer, 0, read)
          }
          sink.flush()
        }
      }
    } catch (_: Throwable) {
      // 只清理本次自己创建的临时文件，不碰任何既有文件。
      temp.delete()
      return Result.Failed("下载中断，未保存。")
    }

    if (written <= 0L) {
      temp.delete()
      return Result.Failed("下载内容为空，未保存。")
    }
    // 有声明长度就必须对得上：socket 正常结束不等于内容完整。
    if (declaredLength >= 0 && written != declaredLength) {
      temp.delete()
      return Result.Failed("下载不完整（收到 $written / 应为 $declaredLength 字节），未保存。")
    }
    if (!looksLikeDecodableImage(temp)) {
      temp.delete()
      return Result.Failed("服务器返回的内容不是可解码的图片，未保存。")
    }

    return Result.Ready(temp, mime, name)
  }

  /**
   * 用平台解码器确认文件确实是图片。
   *
   * `inJustDecodeBounds` 只读尺寸不分配位图，因此超大图也不会撑爆内存。
   * 这里不自己写格式解析器：平台解不出来的就拒绝，不静默保存。
   */
  private fun looksLikeDecodableImage(file: File): Boolean {
    val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    return try {
      BitmapFactory.decodeFile(file.absolutePath, options)
      options.outWidth > 0 && options.outHeight > 0
    } catch (_: Throwable) {
      false
    }
  }

  /**
   * 把临时文件复制到用户通过系统文档流程新建的位置。
   *
   * 用的是「创建新文档」而不是「打开已有文件」，因此不会截断覆盖任何既有文件。
   * 全部写完并成功关闭输出后才算成功。
   */
  fun commit(context: Context, temp: File, target: Uri): CommitResult {
    var opened = false
    return try {
      val stream = context.contentResolver.openOutputStream(target)
      if (stream == null) {
        CommitResult.FAILED_CLEAN
      } else {
        opened = true
        stream.use { sink ->
          temp.inputStream().use { source -> source.copyTo(sink, DEFAULT_BUFFER_SIZE) }
          sink.flush()
        }
        CommitResult.OK
      }
    } catch (_: Throwable) {
      // 写到一半失败时，用户选择的位置上已经有一个不完整文件。
      // 该文件是本次流程刚创建的，归属明确，可以删；删不掉就如实告知，
      // 不能说「未保存」让用户以为那里什么都没有。
      if (opened && deleteTarget(context, target)) {
        CommitResult.FAILED_CLEAN
      } else if (opened) {
        CommitResult.FAILED_PARTIAL
      } else {
        CommitResult.FAILED_CLEAN
      }
    } finally {
      temp.delete()
    }
  }

  /** 写入结果。区分「目标干净」与「目标可能残留」，因为提示语不同。 */
  enum class CommitResult { OK, FAILED_CLEAN, FAILED_PARTIAL }

  /**
   * 删除本次流程创建的目标文档。
   *
   * 只对确认由本次「创建新文档」产生的 URI 调用，不按路径猜测，
   * 更不触碰任何既有文件。
   */
  private fun deleteTarget(context: Context, target: Uri): Boolean =
    try {
      DocumentsContract.deleteDocument(context.contentResolver, target)
    } catch (_: Throwable) {
      false
    }

  /**
   * 「新建文档」契约。
   *
   * 不用现成的 CreateDocument 是因为它在构造时就固定了 MIME，
   * 而这里的类型要等下载校验完才知道。
   */
  class CreateImageDocument : ActivityResultContract<Pair<String, String>, Uri?>() {
    override fun createIntent(context: Context, input: Pair<String, String>): Intent =
      Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
        addCategory(Intent.CATEGORY_OPENABLE)
        type = input.first
        putExtra(Intent.EXTRA_TITLE, input.second)
      }

    override fun parseResult(resultCode: Int, intent: Intent?): Uri? {
      // 取消时某些文档 provider 仍会带回 data，只看 intent 会把取消当成功。
      if (resultCode != Activity.RESULT_OK) return null
      return intent?.data
    }
  }
}
