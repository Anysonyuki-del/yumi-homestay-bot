package icu.akros.yumi

/**
 * 判定「当前页面是否就是打包在应用内的诊断测量页」。
 *
 * 原生测量值只会注入这一个页面。判据是精确的本地资源地址，
 * 而业务页面一律来自 https 远程来源，永远不可能命中，
 * 因此即使这段代码进了正式包，也不会向任何业务页面注入内容。
 *
 * 抽成顶层纯函数是为了能用 JVM 单元测试直接验证这条边界，
 * 而不是靠「读代码觉得没问题」。
 */
object DiagnosticsGate {
  private val ALLOWED = setOf(
    "tauri://localhost/diagnostics.html",
    "http://tauri.localhost/diagnostics.html",
  )

  fun isDiagnosticsPage(rawUrl: String?): Boolean {
    val url = rawUrl ?: return false
    // 去掉查询串与片段后做精确比较：startsWith 会把带参数的伪造地址也放进来。
    val clean = url.substringBefore('?').substringBefore('#')
    return clean in ALLOWED
  }
}
