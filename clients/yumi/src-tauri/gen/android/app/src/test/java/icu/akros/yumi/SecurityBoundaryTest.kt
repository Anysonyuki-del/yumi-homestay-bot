package icu.akros.yumi

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 两条安全边界的 JVM 单元测试：诊断注入门控与图片保存的同源判定。
 *
 * 这两处都是「即使代码进了正式包也必须无害」的地方，
 * 不能只靠读代码断定，必须有可执行的反向证据。
 */
class ImageSaverOriginTest {
  private val workstation = "https://akros.icu/employee/admin"

  @Test
  fun `同源附件允许保存`() {
    assertTrue(ImageSaver.isSaveable("https://akros.icu/employee/private-files/abc", workstation))
    // 显式写出默认端口应与省略等价
    assertTrue(ImageSaver.isSaveable("https://akros.icu:443/x.png", workstation))
  }

  @Test
  fun `非同源与危险地址一律拒绝`() {
    listOf(
      null,
      "",
      "https://akros.icu.evil.example/x.png",
      "https://evil-akros.icu/x.png",
      "http://akros.icu/x.png",
      "https://akros.icu:8443/x.png",
      "https://evil.example/x.png",
      "file:///sdcard/x.png",
      "javascript:alert(1)",
      "content://media/external/images/1",
      // 跨语言一致性：Rust 的 classify_url_against 对带用户信息的 URL 一律
      // 返回 Block，Kotlin 这侧必须同样拒绝，否则同一地址两边判定相反。
      "https://user@akros.icu/x.png",
      "https://user:pw@akros.icu/x.png",
      "https://akros.icu@evil.example/x.png",
    ).forEach {
      assertFalse("不应允许保存 $it", ImageSaver.isSaveable(it, workstation))
    }
  }
}
