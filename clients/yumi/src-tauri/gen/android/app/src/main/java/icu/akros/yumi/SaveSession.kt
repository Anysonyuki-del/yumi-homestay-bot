package icu.akros.yumi

import android.net.Uri
import java.io.File
import java.util.concurrent.atomic.AtomicLong

/**
 * 一次图片保存操作的事务边界。
 *
 * 同一时刻只允许一个在途保存。先前的实现每次长按都开一个线程、
 * 共用一个 `pendingTemp` 字段：两次下载交错时，
 * 后完成的下载会覆盖字段，于是文件选择回调可能把 A 图的内容写进
 * 用户为 B 图选择的位置——串单，而且用户看不出来。
 *
 * 这里给每次操作一个自增 ID，异步回调必须带着自己的 ID 回来；
 * ID 对不上说明该操作已被取代或已随 Activity 失效，
 * 此时只清理它自己创建的资源，绝不碰当前操作的引用。
 */
class SaveSession {

  /** 一次保存操作。`temp` 是本次下载落地的应用私有缓存文件。 */
  data class Operation(val id: Long, val sourceUrl: String, var temp: File? = null)

  private val nextId = AtomicLong(1)
  private var current: Operation? = null

  /** 是否已有在途操作。 */
  val busy: Boolean
    @Synchronized get() = current != null

  /**
   * 开启一次保存；已有在途操作时返回 null，由调用方提示用户。
   * 刻意不排队：用户长按两次通常是手滑，静默排队会让第二张图在很久以后突然弹出选择器。
   */
  @Synchronized
  fun begin(sourceUrl: String): Operation? {
    if (current != null) return null
    return Operation(nextId.getAndIncrement(), sourceUrl).also { current = it }
  }

  /** 回调是否仍属于当前在途操作。 */
  @Synchronized
  fun isCurrent(id: Long): Boolean = current?.id == id

  /** 取当前操作，仅当 ID 匹配。 */
  @Synchronized
  fun operation(id: Long): Operation? = current?.takeIf { it.id == id }

  /**
   * 结束操作并交回其临时文件，供调用方清理。
   * 重复调用返回 null，因此清理不会重复执行。
   */
  @Synchronized
  fun finish(id: Long): File? {
    val op = current ?: return null
    if (op.id != id) return null
    current = null
    return op.temp
  }

  /**
   * 结束当前在途操作并交回临时文件，无在途操作时返回 null。
   *
   * 供系统文档选择器回调使用：同一时刻只可能有一个在途操作，
   * 而 Activity 重建后新实例的会话是空的，回调自然落到 null 分支，
   * 于是提示用户重新保存而不是把内容写进一个来历不明的 URI。
   */
  @Synchronized
  fun finishCurrent(): File? {
    val op = current ?: return null
    current = null
    return op.temp
  }

  /**
   * Activity 销毁时作废在途操作。
   *
   * 返回待清理的临时文件。之后所有旧回调的 ID 都不再匹配，
   * 因此不会再弹选择器、写目标文件或弹 Toast。
   */
  @Synchronized
  fun invalidate(): File? {
    val op = current ?: return null
    current = null
    return op.temp
  }
}
