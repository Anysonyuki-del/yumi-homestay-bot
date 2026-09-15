package icu.akros.yumi

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 诊断注入门控的边界测试。
 *
 * 只在 diagnostics 构建里编译运行——实现本身也只在该模式存在。
 */
class DiagnosticsGateTest {

  @Test
  fun `只认那一个打包诊断页`() {
    assertTrue(DiagnosticsGate.isDiagnosticsPage("tauri://localhost/diagnostics.html"))
    assertTrue(DiagnosticsGate.isDiagnosticsPage("http://tauri.localhost/diagnostics.html"))
  }

  @Test
  fun `业务页面与伪造地址一律不注入`() {
    listOf(
      null,
      "",
      "https://akros.icu/employee/admin",
      "https://akros.icu/diagnostics.html",
      // 把本地路径塞进查询串或片段，企图骗过前缀匹配
      "https://evil.example/?x=tauri://localhost/diagnostics.html",
      "https://evil.example/#tauri://localhost/diagnostics.html",
      "tauri://localhost/diagnostics.htmlx",
      "tauri://localhost.evil.example/diagnostics.html",
      "http://tauri.localhost.evil.example/diagnostics.html",
    ).forEach {
      assertFalse("不应对 $it 注入", DiagnosticsGate.isDiagnosticsPage(it))
    }
  }

  @Test
  fun `带查询串与片段的诊断页仍然算同一个页面`() {
    // WebView 可能在地址上补 ?/# ，去掉后精确匹配，不放宽为前缀匹配。
    assertTrue(DiagnosticsGate.isDiagnosticsPage("tauri://localhost/diagnostics.html?x=1"))
    assertTrue(DiagnosticsGate.isDiagnosticsPage("tauri://localhost/diagnostics.html#top"))
  }
}
