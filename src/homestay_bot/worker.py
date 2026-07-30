import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.integrations.wecom.api_client import WeComApiError
from homestay_bot.integrations.wecom.schemas import SyncMessagePage
from homestay_bot.services.message_service import IncomingMessage


class WorkerJob(Protocol):
    """定义 worker 处理所需的最小任务字段。"""

    job_type: str
    payload: dict[str, Any]


class WorkerRepository[JobType: WorkerJob](Protocol):
    """定义 worker 领取和更新任务状态的接口。"""

    async def claim_next(self) -> JobType | None:
        """领取下一项到期任务。"""

    async def mark_completed(self, job: JobType) -> None:
        """标记任务完成。"""

    async def mark_failed(
        self,
        job: JobType,
        *,
        error_code: str,
        retry_allowed: bool,
        max_attempts: int,
    ) -> None:
        """按任务策略标记失败或延迟重试。"""


JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class RetrySafeJobError(RuntimeError):
    """表示请求确定尚未产生外部副作用，可以有限重试。"""


class DeferredRetryJobError(RetrySafeJobError):
    """表示依赖配置暂不可用，需要长期低频重试。"""


class WeComSyncApi(Protocol):
    """定义同步 worker 所需的企业微信读取接口。"""

    async def sync_messages(
        self,
        *,
        cursor: str,
        token: str,
        open_kfid: str,
        limit: int = 1000,
    ) -> SyncMessagePage:
        """返回一页客服消息。"""


class WeComPollingApi(WeComSyncApi, Protocol):
    """定义定时补拉发现客服账号所需的只读接口。"""

    async def list_kf_account_ids(self) -> list[str]:
        """返回当前企业全部微信客服账号 ID。"""


class WeComSyncJobHandler:
    """同步企业微信消息并转换为内部统一消息。"""

    _origins = {
        3: MessageOrigin.GUEST,
        5: MessageOrigin.SERVICER,
    }

    def __init__(
        self,
        *,
        api: WeComSyncApi,
        handle_message: Callable[[IncomingMessage], Awaitable[None]],
        enqueue: Callable[[str, dict[str, Any]], Awaitable[Any]],
        handle_send_failure: (
            Callable[[str, int], Awaitable[None]] | None
        ) = None,
    ) -> None:
        """注入企业微信读取、消息、发送失败和续页处理边界。"""
        self._api = api
        self._handle_message = handle_message
        self._enqueue = enqueue
        self._handle_send_failure = handle_send_failure

    async def sync_page(
        self,
        *,
        cursor: str,
        token: str,
        open_kfid: str,
    ) -> SyncMessagePage:
        """读取并处理一页消息，同时把下一游标交给调用方。"""
        page = await self._api.sync_messages(
            cursor=cursor,
            token=token,
            open_kfid=open_kfid,
        )
        for item in page.msg_list:
            if item.msgtype == "event" and item.event is not None:
                # 发送失败是平台异步事件，必须按原发送消息编号回写，
                # 不能伪装成一条客人消息进入客服上下文。
                event = item.event
                if (
                    event.get("event_type") == "msg_send_fail"
                    and self._handle_send_failure is not None
                ):
                    failed_message_id = str(
                        event.get("fail_msgid", "")
                    ).strip()
                    fail_type = event.get("fail_type")
                    if failed_message_id and isinstance(fail_type, int):
                        await self._handle_send_failure(
                            failed_message_id,
                            fail_type,
                        )
                continue
            origin = (
                self._origins.get(item.origin)
                if item.origin is not None
                else None
            )
            if (
                origin is None
                or not item.msgid
                or not item.open_kfid
                or not item.external_userid
                or item.send_time is None
            ):
                continue
            content = ""
            if item.msgtype == "text" and item.text is not None:
                content = str(item.text.get("content", ""))
            await self._handle_message(
                IncomingMessage(
                    msgid=item.msgid,
                    open_kfid=item.open_kfid,
                    external_userid=item.external_userid,
                    origin=origin,
                    msgtype=item.msgtype or "unknown",
                    content=content,
                    sent_at=datetime.fromtimestamp(item.send_time, UTC),
                )
            )
        return page

    async def __call__(self, payload: dict[str, Any]) -> None:
        """处理回调触发的一页消息，并把剩余分页持久化入队。"""
        cursor = str(payload.get("cursor", ""))
        token = str(payload["token"])
        open_kfid = str(payload["open_kfid"])
        page = await self.sync_page(
            cursor=cursor,
            token=token,
            open_kfid=open_kfid,
        )

        if page.has_more:
            await self._enqueue(
                "wecom_sync",
                {
                    "cursor": page.next_cursor,
                    "token": token,
                    "open_kfid": open_kfid,
                },
            )


class WeComMessagePoller:
    """在回调缺失时定时补拉客服消息，并按账号维护内存游标。"""

    def __init__(
        self,
        *,
        api: WeComPollingApi,
        handler: WeComSyncJobHandler,
        max_pages_per_poll: int = 100,
        account_refresh_seconds: float = 300.0,
        monotonic_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        """注入企业微信接口、消息处理器和客服账号缓存时钟。"""
        self._api = api
        self._handler = handler
        self._max_pages_per_poll = max_pages_per_poll
        self._account_refresh_seconds = account_refresh_seconds
        self._monotonic_provider = monotonic_provider
        self._account_ids: list[str] | None = None
        self._accounts_expires_at = 0.0
        self._cursors: dict[str, str] = {}

    async def run_once(self) -> None:
        """发现全部客服账号并从各自上次游标补拉到最新页。"""
        now = self._monotonic_provider()
        if self._account_ids is None or now >= self._accounts_expires_at:
            # 客服账号变化频率远低于消息频率，缓存列表可避免五秒轮询
            # 重复消耗账号列表接口额度；到期后仍会发现新增或删除的账号。
            self._account_ids = await self._api.list_kf_account_ids()
            self._accounts_expires_at = now + self._account_refresh_seconds
        account_ids = list(self._account_ids)
        first_error: Exception | None = None
        rate_limit_error: WeComApiError | None = None
        for open_kfid in account_ids:
            try:
                cursor = self._cursors.get(open_kfid, "")
                for _ in range(self._max_pages_per_poll):
                    page = await self._handler.sync_page(
                        cursor=cursor,
                        token="",
                        open_kfid=open_kfid,
                    )
                    if page.next_cursor:
                        cursor = page.next_cursor
                    self._cursors[open_kfid] = cursor
                    if not page.has_more:
                        break
                    if not page.next_cursor:
                        raise RuntimeError("企业微信补拉声明有更多页但缺少游标")
            except Exception as error:
                # 单个账号故障不得阻断其他客服账号的消息补拉。
                # 限流需要至少 60 秒退避，优先级高于同轮次的普通异常。
                if (
                    isinstance(error, WeComApiError)
                    and error.error_code == 45009
                ):
                    rate_limit_error = error
                elif first_error is None:
                    first_error = error
        if rate_limit_error is not None:
            raise rate_limit_error
        if first_error is not None:
            raise first_error


class Worker[JobType: WorkerJob]:
    """执行持久化任务，并对外部写入采用禁止重放策略。"""

    _retryable_job_types = {
        "wecom_sync",
        "hostex_read",
        "hostex_event",
        "faq_draft_generate",
        "customer_tag_sync",
    }

    def __init__(
        self,
        *,
        repository: WorkerRepository[JobType],
        handlers: dict[str, JobHandler],
        heartbeat: Callable[[datetime], None] | None = None,
        checkpoint: Callable[[], Awaitable[None]] | None = None,
        on_job_committed: Callable[[JobType], None] | None = None,
    ) -> None:
        """注入任务仓储、处理器、提交边界和成功提交回调。"""
        self._repository = repository
        self._handlers = handlers
        self._heartbeat = heartbeat
        self._checkpoint = checkpoint
        self._on_job_committed = on_job_committed

    async def run_once(self) -> bool:
        """领取并处理一项任务；没有到期任务时返回 False。"""
        if self._heartbeat is not None:
            self._heartbeat(datetime.now(UTC))
        job = await self._repository.claim_next()
        if job is None:
            return False
        if self._checkpoint is not None:
            # 先提交 RUNNING 状态，进程中断后才能由超时锁恢复。
            await self._checkpoint()

        handler = self._handlers.get(job.job_type)
        if handler is None:
            await self._repository.mark_failed(
                job,
                error_code="unknown_job_type",
                retry_allowed=False,
                max_attempts=1,
            )
            if self._checkpoint is not None:
                await self._checkpoint()
            return True

        succeeded = False
        try:
            await handler(job.payload)
        except Exception as error:
            max_attempts = (
                10_000 if isinstance(error, DeferredRetryJobError) else 3
            )
            # 错误码只记录异常类型，避免把可能含客人信息的正文写进任务表。
            await self._repository.mark_failed(
                job,
                error_code=type(error).__name__,
                retry_allowed=(
                    job.job_type in self._retryable_job_types
                    or isinstance(error, RetrySafeJobError)
                ),
                max_attempts=max_attempts,
            )
        else:
            await self._repository.mark_completed(job)
            succeeded = True
        if self._checkpoint is not None:
            await self._checkpoint()
        if succeeded and self._on_job_committed is not None:
            # 只有业务结果和任务完成状态都提交成功后，才向应用报告成功。
            self._on_job_committed(job)
        return True


async def run_forever(
    worker_factory: Callable[[], Awaitable[Worker[Any]]],
    *,
    poll_interval_seconds: float = 1.0,
) -> None:
    """持续创建短生命周期 worker 执行任务，空闲时有限等待。"""
    while True:
        worker = await worker_factory()
        handled = await worker.run_once()
        if not handled:
            await asyncio.sleep(poll_interval_seconds)
