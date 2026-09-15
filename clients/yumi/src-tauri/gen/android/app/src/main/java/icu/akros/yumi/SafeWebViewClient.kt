package icu.akros.yumi

import android.graphics.Bitmap
import android.os.Message
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * 包在 Tauri 自带 WebViewClient 外面的一层，只补两件它没做的事：
 * 禁止表单重新提交，以及把主框架加载失败交给原生错误界面。
 *
 * **为什么是委托而不是继承**：`RustWebViewClient` 是 Kotlin 默认的 final 类，
 * 且位于每次构建都会重新生成的 generated/ 目录，既不能继承也不能修改。
 * 直接给 WebView 换一个全新的 client 会丢掉 Tauri 的资源拦截与导航策略
 * （`shouldInterceptRequest` 走 Rust 的资源加载，`shouldOverrideUrlLoading`
 * 走 Rust 的 `on_navigation` 导航判定），那等于把安全边界一起换掉。
 * 因此这里逐个转发它实际覆盖的方法，其余沿用基类默认实现。
 *
 * **维护约束**：转发清单必须与 `generated/RustWebViewClient.kt` 的 override 列表一致。
 * 升级 Tauri/wry 后如果那边新增了 override 而这里没跟上，对应行为会静默丢失。
 * 升级时必须重新比对这两处。
 */
class SafeWebViewClient(
  private val inner: WebViewClient,
  private val onMainFrameError: (WebView, WebResourceRequest, WebResourceError) -> Boolean,
) : WebViewClient() {

  // ---- 本层新增的行为 ----

  /**
   * 永远不重新提交表单。
   *
   * Spec F-03 要求返回路径不得重放 POST。Android 的默认实现也是不重发，
   * 但那是隐式依赖；这里显式写出来，让这条安全属性可读、可审查，
   * 不依赖某个版本的默认值恰好正确。
   */
  override fun onFormResubmission(view: WebView, dontResend: Message, resend: Message) {
    dontResend.sendToTarget()
  }

  /**
   * 主框架加载失败时交给宿主决定是否显示原生错误界面。
   *
   * 只处理主框架：子资源（图片、CSS）失败不该把整页换成错误页。
   * 宿主返回 true 表示已接管，不再走 Tauri 原有处理。
   */
  override fun onReceivedError(
    view: WebView,
    request: WebResourceRequest,
    error: WebResourceError,
  ) {
    if (request.isForMainFrame && onMainFrameError(view, request, error)) {
      return
    }
    inner.onReceivedError(view, request, error)
  }

  // ---- 以下为原样转发，保持 Tauri 行为不变 ----

  override fun shouldInterceptRequest(
    view: WebView,
    request: WebResourceRequest,
  ): WebResourceResponse? = inner.shouldInterceptRequest(view, request)

  override fun shouldOverrideUrlLoading(
    view: WebView,
    request: WebResourceRequest,
  ): Boolean = inner.shouldOverrideUrlLoading(view, request)

  override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
    inner.onPageStarted(view, url, favicon)
  }

  override fun onPageFinished(view: WebView, url: String) {
    inner.onPageFinished(view, url)
  }
}
