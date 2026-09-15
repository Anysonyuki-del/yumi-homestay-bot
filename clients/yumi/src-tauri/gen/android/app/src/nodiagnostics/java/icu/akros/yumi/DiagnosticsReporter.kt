package icu.akros.yumi

import android.app.Activity
import android.webkit.WebView

/**
 * 非诊断构建的空实现。
 *
 * 这里没有采集、没有注入、也没有轮询：正式包里根本不存在可执行的测量入口，
 * 而不是靠运行时判断绕开它。由 identity.gradle 按构建模式选择源码集。
 */
object DiagnosticsReporter {
  const val ENABLED = false

  fun install(activity: Activity, webView: WebView) = Unit

  fun onSafeArea(activity: Activity, webView: WebView, metrics: SafeAreaMetrics) = Unit
}
