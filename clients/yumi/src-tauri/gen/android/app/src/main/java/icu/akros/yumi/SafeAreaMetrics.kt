package icu.akros.yumi

/**
 * 一次窗口 inset 派发算出的安全区结果。
 *
 * 纯数据，不含任何采集或注入逻辑，因此各构建模式共用。
 */
data class SafeAreaMetrics(
  val left: Int,
  val top: Int,
  val right: Int,
  val bottom: Int,
  val barsTop: Int,
  val barsBottom: Int,
  val cutoutTop: Int,
  val cutoutBottom: Int,
  val imeBottom: Int,
  val imeVisible: Boolean,
  val usedMargin: Boolean,
)
