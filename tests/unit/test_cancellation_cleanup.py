"""验证必须完成清理在重复取消和异常下的稳定语义。"""

import asyncio

import pytest

from homestay_bot.services.cancellation import complete_cleanup


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_cleanup_and_preserves_cancel_count() -> None:
    """任意重复cancel都不能丢弃清理task，完成后保留取消计数并传播取消。"""
    started = asyncio.Event()
    proceed = asyncio.Event()
    completed = asyncio.Event()

    async def cleanup() -> None:
        """等待放行并记录清理实际完成。"""
        started.set()
        await proceed.wait()
        completed.set()

    task = asyncio.create_task(complete_cleanup(cleanup()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    proceed.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()
    assert task.cancelling() == 2


@pytest.mark.asyncio
async def test_original_cancel_wins_after_cleanup_error() -> None:
    """清理自身失败仍先完成，并以cause保留异常后重新传播原取消。"""
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def cleanup() -> None:
        """等待取消到达后模拟清理异常。"""
        started.set()
        await proceed.wait()
        raise RuntimeError("cleanup secret must not be logged")

    task = asyncio.create_task(complete_cleanup(cleanup()))
    await started.wait()
    task.cancel()
    proceed.set()

    with pytest.raises(asyncio.CancelledError) as captured:
        await task
    assert isinstance(captured.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_cleanup_error_without_cancel_is_propagated() -> None:
    """没有外层取消时不得吞掉清理自身异常。"""

    async def cleanup() -> None:
        """模拟立即失败的清理。"""
        raise LookupError("cleanup failed")

    with pytest.raises(LookupError, match="cleanup failed"):
        await complete_cleanup(cleanup())
