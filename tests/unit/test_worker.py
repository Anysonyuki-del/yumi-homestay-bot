from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from homestay_bot.application import _next_wecom_poll_delay
from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.integrations.wecom.api_client import WeComApiError
from homestay_bot.worker import (
    DeferredRetryJobError,
    RetrySafeJobError,
    WeComMessagePoller,
    WeComSyncJobHandler,
    Worker,
)


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
async def test_deferred_message_failure_is_retryable() -> None:
    """最终回复生成的暂时性失败应允许有限重试。"""
    repository = RepositoryStub(
        JobStub(job_type="wecom_process_message", payload={"msgid": "msg-1"})
    )

    async def handler(payload):
        """模拟最终生成遇到暂时性异常。"""
        raise RuntimeError("temporary")

    worker = Worker(
        repository=repository,
        handlers={"wecom_process_message": handler},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is True


@pytest.mark.asyncio
async def test_worker_reports_success_only_after_final_commit() -> None:
    """成功回调必须发生在任务完成状态真正提交之后。"""
    job = JobStub(job_type="hostex_event")
    repository = RepositoryStub(job)
    sequence: list[str] = []

    async def handler(payload):
        """记录业务处理完成。"""
        sequence.append("handled")

    async def checkpoint() -> None:
        """记录领取提交和最终完成提交。"""
        sequence.append("committed")

    def on_job_committed(committed_job) -> None:
        """记录最终提交后的成功通知。"""
        assert committed_job is job
        sequence.append("reported")

    worker = Worker(
        repository=repository,
        handlers={"hostex_event": handler},
        checkpoint=checkpoint,
        on_job_committed=on_job_committed,
    )

    await worker.run_once()

    assert sequence == [
        "committed",
        "handled",
        "committed",
        "reported",
    ]


@pytest.mark.asyncio
async def test_worker_does_not_report_success_when_final_commit_fails() -> None:
    """最终提交失败时不得提前刷新外部同步成功心跳。"""
    repository = RepositoryStub(JobStub(job_type="hostex_event"))
    checkpoint_calls = 0
    reported: list[str] = []

    async def handler(payload):
        """模拟业务处理本身成功。"""

    async def checkpoint() -> None:
        """领取提交成功、任务完成提交失败。"""
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise RuntimeError("commit failed")

    worker = Worker(
        repository=repository,
        handlers={"hostex_event": handler},
        checkpoint=checkpoint,
        on_job_committed=lambda job: reported.append(job.job_type),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await worker.run_once()

    assert reported == []


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
async def test_worker_never_retries_uncertain_credential_send() -> None:
    """凭证发送出现未捕获异常也不得由通用 worker 自动重放。"""
    repository = RepositoryStub(
        JobStub(
            job_type="credential_send_part",
            payload={"part_id": 51},
        )
    )

    async def uncertain_handler(payload):
        """模拟外部发送后进程异常。"""
        raise TimeoutError("unknown external result")

    worker = Worker(
        repository=repository,
        handlers={"credential_send_part": uncertain_handler},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is False


@pytest.mark.asyncio
async def test_worker_retries_only_explicitly_safe_send_failure() -> None:
    """发送任务只有在确认尚未产生外部副作用时才允许有限重试。"""
    repository = RepositoryStub(JobStub(job_type="wecom_send_text"))

    async def safely_failed(payload):
        """模拟连接尚未建立的确定失败。"""
        raise RetrySafeJobError("not connected")

    worker = Worker(
        repository=repository,
        handlers={"wecom_send_text": safely_failed},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is True


@pytest.mark.asyncio
async def test_worker_retries_faq_draft_generation_as_safe_read_work() -> None:
    """FAQ 草稿生成没有外部业务写入，普通模型失败允许有限重试。"""
    repository = RepositoryStub(
        JobStub(job_type="faq_draft_generate", payload={"candidate_id": 7})
    )

    async def failing_handler(payload):
        """模拟 DeepSeek 暂时失败。"""
        raise TimeoutError("temporary")

    worker = Worker(
        repository=repository,
        handlers={"faq_draft_generate": failing_handler},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is True
    assert repository.failure["max_attempts"] == 3


@pytest.mark.asyncio
async def test_worker_retries_idempotent_customer_tag_sync() -> None:
    """企业微信客户标签采用目标状态写入，暂时失败允许有限重试。"""
    repository = RepositoryStub(
        JobStub(job_type="customer_tag_sync", payload={"customer_id": 7})
    )

    async def failing_handler(payload):
        """模拟企业微信客户联系接口暂时不可用。"""
        raise TimeoutError("temporary")

    worker = Worker(
        repository=repository,
        handlers={"customer_tag_sync": failing_handler},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is True
    assert repository.failure["max_attempts"] == 3


@pytest.mark.asyncio
async def test_worker_keeps_deferred_admin_notification_retriable() -> None:
    """管理员暂不可用时应长期保留任务，等待管理员配置恢复。"""
    repository = RepositoryStub(
        JobStub(job_type="faq_draft_generate", payload={"candidate_id": 7})
    )

    async def deferred_handler(payload):
        """模拟当前没有启用管理员。"""
        raise DeferredRetryJobError("no active administrator")

    worker = Worker(
        repository=repository,
        handlers={"faq_draft_generate": deferred_handler},
    )

    await worker.run_once()

    assert repository.failure["retry_allowed"] is True
    assert repository.failure["max_attempts"] == 10_000


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


@pytest.mark.asyncio
async def test_wecom_sync_routes_send_failure_event_without_guest_message() -> None:
    """消息发送失败事件必须按 fail_msgid 交给投递状态处理器。"""
    page = SimpleNamespace(
        msg_list=[
            SimpleNamespace(
                msgid="event-fail-1",
                open_kfid=None,
                external_userid=None,
                send_time=1785283200,
                origin=None,
                msgtype="event",
                text=None,
                event={
                    "event_type": "msg_send_fail",
                    "open_kfid": "wk-1",
                    "external_userid": "wm-1",
                    "fail_msgid": "msg-accepted",
                    "fail_type": 10,
                },
            )
        ],
        has_more=0,
        next_cursor="",
    )

    class ApiStub:
        """返回固定发送失败事件页。"""

        async def sync_messages(self, **kwargs):
            """返回单个失败事件。"""
            return page

    messages = []
    failures = []

    async def handle_message(message):
        """事件不得伪装成客户消息。"""
        messages.append(message)

    async def handle_send_failure(message_id, fail_type):
        """记录准确平台消息编号和失败类型。"""
        failures.append((message_id, fail_type))

    async def enqueue(job_type, payload):
        """单页事件不创建续页任务。"""

    handler = WeComSyncJobHandler(
        api=ApiStub(),
        handle_message=handle_message,
        handle_send_failure=handle_send_failure,
        enqueue=enqueue,
    )

    await handler.sync_page(cursor="", token="", open_kfid="wk-1")

    assert messages == []
    assert failures == [("msg-accepted", 10)]


@pytest.mark.asyncio
async def test_wecom_poller_discovers_accounts_and_reuses_cursor() -> None:
    """定时补拉应自动发现客服账号，并在后续轮次沿用各自游标。"""
    sync_calls: list[dict[str, str]] = []
    account_list_calls = 0

    class ApiStub:
        """返回一个客服账号和连续推进的同步游标。"""

        async def list_kf_account_ids(self) -> list[str]:
            """模拟企业微信客服账号列表。"""
            nonlocal account_list_calls
            account_list_calls += 1
            return ["wk-1"]

        async def sync_messages(self, **kwargs):
            """记录游标并返回下一游标。"""
            sync_calls.append(kwargs)
            next_cursor = (
                "cursor-1" if kwargs["cursor"] == "" else "cursor-2"
            )
            return SimpleNamespace(
                msg_list=[],
                has_more=0,
                next_cursor=next_cursor,
            )

    async def handle_message(message):
        """本测试没有消息需要处理。"""

    async def enqueue(job_type, payload):
        """轮询自行维护游标，不应创建回调续页任务。"""
        raise AssertionError("轮询不应创建续页任务")

    api = ApiStub()
    handler = WeComSyncJobHandler(
        api=api,
        handle_message=handle_message,
        enqueue=enqueue,
    )
    poller = WeComMessagePoller(api=api, handler=handler)

    await poller.run_once()
    await poller.run_once()

    assert [call["cursor"] for call in sync_calls] == ["", "cursor-1"]
    assert all(call["token"] == "" for call in sync_calls)
    assert account_list_calls == 1


@pytest.mark.asyncio
async def test_wecom_poller_refreshes_cached_accounts_after_five_minutes() -> None:
    """客服账号缓存满五分钟后应刷新，避免长期遗漏新增账号。"""
    clock = iter([0.0, 299.0, 300.0])
    account_list_calls = 0

    class ApiStub:
        """记录客服账号列表刷新次数。"""

        async def list_kf_account_ids(self) -> list[str]:
            """返回固定客服账号。"""
            nonlocal account_list_calls
            account_list_calls += 1
            return ["wk-1"]

        async def sync_messages(self, **kwargs):
            """返回空消息页并保留游标。"""
            return SimpleNamespace(
                msg_list=[],
                has_more=0,
                next_cursor=kwargs["cursor"] or "cursor-1",
            )

    async def handle_message(message):
        """本测试没有消息需要处理。"""

    async def enqueue(job_type, payload):
        """轮询不应创建回调续页任务。"""
        raise AssertionError("轮询不应创建续页任务")

    api = ApiStub()
    handler = WeComSyncJobHandler(
        api=api,
        handle_message=handle_message,
        enqueue=enqueue,
    )
    poller = WeComMessagePoller(
        api=api,
        handler=handler,
        monotonic_provider=lambda: next(clock),
    )

    await poller.run_once()
    await poller.run_once()
    await poller.run_once()

    assert account_list_calls == 2


@pytest.mark.asyncio
async def test_wecom_poller_checkpoints_cursor_at_page_batch_limit() -> None:
    """积压超过单批页数时应保存进度，下轮从新游标继续。"""
    cursors: list[str] = []

    class ApiStub:
        """持续返回更多页，用于验证批次游标检查点。"""

        async def list_kf_account_ids(self) -> list[str]:
            """返回一个有大量积压的客服账号。"""
            return ["wk-1"]

        async def sync_messages(self, **kwargs):
            """按调用顺序推进游标并始终声明还有更多页。"""
            cursors.append(kwargs["cursor"])
            return SimpleNamespace(
                msg_list=[],
                has_more=1,
                next_cursor=f"cursor-{len(cursors)}",
            )

    async def handle_message(message):
        """本测试没有消息需要处理。"""

    async def enqueue(job_type, payload):
        """轮询不使用回调续页任务。"""

    api = ApiStub()
    poller = WeComMessagePoller(
        api=api,
        handler=WeComSyncJobHandler(
            api=api,
            handle_message=handle_message,
            enqueue=enqueue,
        ),
        max_pages_per_poll=2,
    )

    await poller.run_once()
    await poller.run_once()

    assert cursors == ["", "cursor-1", "cursor-2", "cursor-3"]


@pytest.mark.asyncio
async def test_wecom_poller_isolates_account_failure() -> None:
    """一个客服账号失败时仍应继续补拉其他正常账号。"""
    visited_accounts: list[str] = []

    class ApiStub:
        """让首个账号失败、第二个账号成功。"""

        async def list_kf_account_ids(self) -> list[str]:
            """返回两个客服账号。"""
            return ["wk-broken", "wk-healthy"]

        async def sync_messages(self, **kwargs):
            """记录访问并按账号制造结果。"""
            visited_accounts.append(kwargs["open_kfid"])
            if kwargs["open_kfid"] == "wk-broken":
                raise TimeoutError("temporary")
            return SimpleNamespace(
                msg_list=[],
                has_more=0,
                next_cursor="healthy-cursor",
            )

    async def handle_message(message):
        """本测试没有消息需要处理。"""

    async def enqueue(job_type, payload):
        """轮询不使用回调续页任务。"""

    api = ApiStub()
    poller = WeComMessagePoller(
        api=api,
        handler=WeComSyncJobHandler(
            api=api,
            handle_message=handle_message,
            enqueue=enqueue,
        ),
    )

    with pytest.raises(TimeoutError):
        await poller.run_once()

    assert visited_accounts == ["wk-broken", "wk-healthy"]


@pytest.mark.asyncio
async def test_wecom_poller_prioritizes_rate_limit_across_accounts() -> None:
    """多账号同时失败时应优先传播 45009，以触发至少 60 秒退避。"""

    class ApiStub:
        """依次制造普通超时和企业微信限流。"""

        async def list_kf_account_ids(self) -> list[str]:
            """返回两个会失败的客服账号。"""
            return ["wk-timeout", "wk-limited"]

        async def sync_messages(self, **kwargs):
            """按客服账号返回对应异常。"""
            if kwargs["open_kfid"] == "wk-timeout":
                raise TimeoutError("temporary")
            raise WeComApiError(45009, "rate limit")

    async def handle_message(message):
        """本测试没有消息需要处理。"""

    async def enqueue(job_type, payload):
        """轮询不使用回调续页任务。"""

    api = ApiStub()
    poller = WeComMessagePoller(
        api=api,
        handler=WeComSyncJobHandler(
            api=api,
            handle_message=handle_message,
            enqueue=enqueue,
        ),
    )

    with pytest.raises(WeComApiError) as error:
        await poller.run_once()

    assert error.value.error_code == 45009


def test_wecom_poll_limit_uses_exponential_backoff() -> None:
    """无 Token 补拉被限流后应从 60 秒开始指数退避。"""
    first_delay = _next_wecom_poll_delay(
        current_delay=15,
        interval_seconds=15,
        error=WeComApiError(45009, "rate limit"),
    )
    second_delay = _next_wecom_poll_delay(
        current_delay=first_delay,
        interval_seconds=15,
        error=WeComApiError(45009, "rate limit"),
    )

    assert first_delay == 60
    assert second_delay == 120
