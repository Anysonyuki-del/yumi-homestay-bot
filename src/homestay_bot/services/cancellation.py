"""提供取消期间也必须完成的异步清理原语。"""

import asyncio
from collections.abc import Awaitable
from typing import Any

# 事件循环只弱引用task；集合确保清理完成前始终持有强引用。
_TRACKED_CLEANUP_TASKS: set[asyncio.Task[Any]] = set()


async def complete_cleanup[T](
    cleanup: Awaitable[T],
    *,
    pending_cancel: asyncio.CancelledError | None = None,
) -> T:
    """屏蔽重复取消直至清理完成，随后恢复原取消或返回清理结果。"""
    cleanup_task = asyncio.ensure_future(cleanup)
    _TRACKED_CLEANUP_TASKS.add(cleanup_task)
    original_cancel = pending_cancel
    try:
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as error:
                if cleanup_task.done():
                    # task自身取消由result原样传播；外层取消且清理已完成则保留外层取消。
                    if not cleanup_task.cancelled() and original_cancel is None:
                        original_cancel = error
                    break
                if original_cancel is None:
                    original_cancel = error
            except BaseException:
                # 清理异常统一在task.result读取，才能让已记录的外层取消保持优先。
                break

        try:
            result = cleanup_task.result()
        except BaseException as cleanup_error:
            if original_cancel is not None:
                raise original_cancel from cleanup_error
            raise
        if original_cancel is not None:
            raise original_cancel
        return result
    finally:
        _TRACKED_CLEANUP_TASKS.discard(cleanup_task)
