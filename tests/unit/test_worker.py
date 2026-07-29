from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.worker import WeComSyncJobHandler, Worker


@dataclass
class JobStub:
    """表示 worker 测试中的最小任务。"""

    id: int = 1
    job_type: str = "wecom_sync"
    payload: dict = None

    def __post_init__(self) -> None:
        """为 payload 提供独立默认字典。"""
        if self.payload is None:
            self.payload = {"token": "sync"}


class RepositoryStub:
    """记录 worker 对任务状态的处理。"""

    def __init__(self, job: JobStub) -> None:
        self.job = job
        self.completed = False
        self.failure = None

    async def claim_next(self):
        """返回一次任务后置空。"""
        job, self.job = self.job, None
        return job

    async def mark_completed(self, job) -> None:
        """记录完成状态。"""
        self.completed = True

    async def mark_failed(self, job, **kwargs) -> None:
        """记录失败策略。"""
        self.failure = kwargs


@pytest.mark.asyncio
async def test_worker_executes_registered_handler() -> None:
    """worker 应领取任务、调用处理器并标记完成。"""
    repository = RepositoryStub(JobStub())
    calls = []

    async def handler(payload):
        """记录任务载荷。"""
        calls.append(payload)

    worker = Worker(repository=repository, handlers={"wecom_sync": handler})

    handled = await worker.run_once()

    assert handled is True
    assert calls == [{"token": "sync"}]
    assert repository.completed is True


@pytest.mark.asyncio
async def test_worker_never_retries_hostex_create_job() -> None:
    """百居易创建订单任务失败后必须直接转失败，不能自动重放。"""
    repository = RepositoryStub(
        JobStub(job_type="hostex_create_reservation", payload={"approval_id": 1})
    )

    async def failing_handler(payload):
        """模拟外部写入结果不明确。"""
        raise TimeoutError("timeout")

    worker = Worker(
        repository=repository,
        handlers={"hostex_create_reservation": failing_handler},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is False


@pytest.mark.asyncio
async def test_wecom_sync_maps_guest_and_servicer_origins_without_loop() -> None:
    """企业微信来源 3 和 5 应分别映射为客人和人工客服。"""
    page = SimpleNamespace(
        msg_list=[
            SimpleNamespace(
                msgid="guest-1",
                open_kfid="wk-1",
                external_userid="wm-1",
                send_time=1785283200,
                origin=3,
                msgtype="text",
                text={"content": "你好"},
            ),
            SimpleNamespace(
                msgid="staff-1",
                open_kfid="wk-1",
                external_userid="wm-1",
                send_time=1785283201,
                origin=5,
                msgtype="text",
                text={"content": "人工回复"},
            ),
        ],
        has_more=0,
        next_cursor="",
    )

    class ApiStub:
        """返回固定同步页。"""

        async def sync_messages(self, **kwargs):
            """捕获参数并返回消息。"""
            return page

    handled = []

    async def handle_message(message):
        """记录转换后的统一消息。"""
        handled.append(message)

    async def enqueue(job_type, payload):
        """本页无更多消息，不应继续入队。"""
        raise AssertionError("不应继续入队")

    handler = WeComSyncJobHandler(
        api=ApiStub(),
        handle_message=handle_message,
        enqueue=enqueue,
    )

    await handler(
        {"cursor": "", "token": "sync-token", "open_kfid": "wk-1"}
    )

    assert [item.origin for item in handled] == [
        MessageOrigin.GUEST,
        MessageOrigin.SERVICER,
    ]
