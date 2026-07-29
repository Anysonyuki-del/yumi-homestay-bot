import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from homestay_bot.domain.enums import MessageOrigin
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
    ) -> None:
        """注入企业微信读取端口、会话处理器和续页入队函数。"""
        self._api = api
        self._handle_message = handle_message
        self._enqueue = enqueue

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
            origin = (
                self._origins.get(item.origin)
                if item.origin is not None
                else None
            )
            if (
                origin is None
                or not item.msgid
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
    ) -> None:
        """注入只读企业微信接口和现有消息处理器。"""
        self._api = api
        self._handler = handler
        self._max_pages_per_poll = max_pages_per_poll
        self._cursors: dict[str, str] = {}

    async def run_once(self) -> None:
        """发现全部客服账号并从各自上次游标补拉到最新页。"""
        account_ids = await self._api.list_kf_account_ids()
        for open_kfid in account_ids:
            cursor = self._cursors.get(open_kfid, "")
            for _ in range(self._max_pages_per_poll):
                page = await self._handler.sync_page(
                    cursor=cursor,
                    token="",
                    open_kfid=open_kfid,
                )
                if page.next_cursor:
                    cursor = page.next_cursor
                if not page.has_more:
                    self._cursors[open_kfid] = cursor
                    break
            else:
                raise RuntimeError("企业微信单次补拉分页超过安全上限")


class Worker[JobType: WorkerJob]:
    """执行持久化任务，并对外部写入采用禁止重放策略。"""

    _retryable_job_types = {
        "wecom_sync",
        "hostex_read",
    }

    def __init__(
        self,
        *,
        repository: WorkerRepository[JobType],
        handlers: dict[str, JobHandler],
        heartbeat: Callable[[datetime], None] | None = None,
        checkpoint: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """注入任务仓储、处理器和可选心跳接收器。"""
        self._repository = repository
        self._handlers = handlers
        self._heartbeat = heartbeat
        self._checkpoint = checkpoint

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

        try:
            await handler(job.payload)
        except Exception as error:
            # 错误码只记录异常类型，避免把可能含客人信息的正文写进任务表。
            await self._repository.mark_failed(
                job,
                error_code=type(error).__name__,
                retry_allowed=(
                    job.job_type in self._retryable_job_types
                    or isinstance(error, RetrySafeJobError)
                ),
                max_attempts=3,
            )
        else:
            await self._repository.mark_completed(job)
        if self._checkpoint is not None:
            await self._checkpoint()
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
