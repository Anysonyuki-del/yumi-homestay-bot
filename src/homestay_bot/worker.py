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
JobFailureHandler = Callable[[Any, str, dict[str, Any]], Awaitable[None]]


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


class SyncCursorStore(Protocol):
    """按客服账号保存消息同步游标。"""

    async def load(self, open_kfid: str) -> str:
        """返回上次同步到的位置；没有时为空字符串。"""

    async def save(self, open_kfid: str, cursor: str) -> None:
        """记录已处理完的位置。"""


class InMemorySyncCursorStore:
    """进程内游标，只供没有数据库的装配（测试）使用；生产用数据库实现。"""

    def __init__(self) -> None:
        """初始化空游标表。"""
        self._cursors: dict[str, str] = {}

    async def load(self, open_kfid: str) -> str:
        """返回内存里的游标。"""
        return self._cursors.get(open_kfid, "")

    async def save(self, open_kfid: str, cursor: str) -> None:
        """覆盖内存里的游标。"""
        self._cursors[open_kfid] = cursor


# 同一客服账号同一时间只允许一次同步：回调任务和定时补拉都在本进程里，若同时读到
# 同一游标会把同一页处理两遍。ponytail: 进程内锁，只在单个 API 容器时成立；多实例
# 部署时要换成数据库行锁。
_SYNC_LOCKS: dict[str, asyncio.Lock] = {}


def _sync_lock(open_kfid: str) -> asyncio.Lock:
    """取该客服账号的同步锁。"""
    return _SYNC_LOCKS.setdefault(open_kfid, asyncio.Lock())


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
        handle_session_end: (
            Callable[[str, str, str, datetime | None], Awaitable[None]] | None
        ) = None,
        cursor_store: SyncCursorStore | None = None,
        max_pages: int = 100,
    ) -> None:
        """注入企业微信读取、消息、发送失败、会话结束、续页和游标存储边界。"""
        self._cursor_store = cursor_store or InMemorySyncCursorStore()
        self._max_pages = max_pages
        self._api = api
        self._handle_message = handle_message
        self._enqueue = enqueue
        self._handle_send_failure = handle_send_failure
        self._handle_session_end = handle_session_end

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
                if (
                    event.get("event_type") == "session_status_change"
                    and event.get("change_type") == 3
                    and self._handle_session_end is not None
                    and event.get("open_kfid")
                    and event.get("external_userid")
                ):
                    # 管家在企业微信里点了「结束聊天」：交还机器人并凭 msg_code 发结束语。
                    # 事件没有去重：空游标同步会从几天前重放，发生时间交给下游判断新旧。
                    await self._handle_session_end(
                        str(event["open_kfid"]),
                        str(event["external_userid"]),
                        str(event.get("msg_code", "")),
                        datetime.fromtimestamp(item.send_time, UTC)
                        if item.send_time is not None
                        else None,
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
            metadata: dict[str, str] = {}
            if item.msgtype == "text" and item.text is not None:
                content = str(item.text.get("content", ""))
            elif item.msgtype in {"image", "voice", "video", "file", "location"}:
                # 只保留平台不透明编号，禁止落库外部 URL、媒体正文或原始定位信息。
                media_fields = getattr(item, item.msgtype, None)
                if isinstance(media_fields, dict):
                    media_id = media_fields.get("media_id")
                    if isinstance(media_id, str) and media_id:
                        metadata["media_id"] = media_id[:256]
            await self._handle_message(
                IncomingMessage(
                    msgid=item.msgid,
                    open_kfid=item.open_kfid,
                    external_userid=item.external_userid,
                    origin=origin,
                    msgtype=item.msgtype or "unknown",
                    content=content,
                    metadata=metadata,
                    sent_at=datetime.fromtimestamp(item.send_time, UTC),
                )
            )
        return page

    async def sync_from_saved_cursor(self, *, token: str, open_kfid: str) -> None:
        """从保存的游标接着同步到最新，每处理完一页立即保存游标。

        回调同步与定时补拉共用。事件没有去重：只要不从头读，旧的「结束聊天」
        「发送失败」就不会再被处理一遍。处理到一半中断时，最多重放未保存的那一页。
        """
        async with _sync_lock(open_kfid):
            cursor = await self._cursor_store.load(open_kfid)
            for _ in range(self._max_pages):
                page = await self.sync_page(cursor=cursor, token=token, open_kfid=open_kfid)
                if page.next_cursor and page.next_cursor != cursor:
                    cursor = page.next_cursor
                    await self._cursor_store.save(open_kfid, cursor)
                if not page.has_more:
                    return
                if not page.next_cursor:
                    raise RuntimeError("企业微信同步声明有更多页但缺少游标")

    async def __call__(self, payload: dict[str, Any]) -> None:
        """处理回调触发的同步：从保存的游标接着读完。

        带游标的载荷是上线前入队的旧续页任务，仍按原方式处理这一页并续页。
        """
        cursor = str(payload.get("cursor", ""))
        token = str(payload["token"])
        open_kfid = str(payload["open_kfid"])
        if not cursor:
            await self.sync_from_saved_cursor(token=token, open_kfid=open_kfid)
            return
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
    """在回调缺失时定时补拉客服消息；游标与回调同步共用处理器里的存储。"""

    def __init__(
        self,
        *,
        api: WeComPollingApi,
        handler: WeComSyncJobHandler,
        account_refresh_seconds: float = 300.0,
        monotonic_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        """注入企业微信接口、消息处理器和客服账号缓存时钟。"""
        self._api = api
        self._handler = handler
        self._account_refresh_seconds = account_refresh_seconds
        self._monotonic_provider = monotonic_provider
        self._account_ids: list[str] | None = None
        self._accounts_expires_at = 0.0

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
                await self._handler.sync_from_saved_cursor(token="", open_kfid=open_kfid)
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
        "complaint_review_generate",
        "guest_delivery_rewrite",
        "customer_tag_sync",
        "customer_context_refresh",
        "wecom_process_message",
    }

    def __init__(
        self,
        *,
        repository: WorkerRepository[JobType],
        handlers: dict[str, JobHandler],
        heartbeat: Callable[[datetime], None] | None = None,
        checkpoint: Callable[[], Awaitable[None]] | None = None,
        on_job_committed: Callable[[JobType], None] | None = None,
        on_job_failed: JobFailureHandler | None = None,
    ) -> None:
        """注入任务仓储、处理器、提交边界和成功提交回调。"""
        self._repository = repository
        self._handlers = handlers
        self._heartbeat = heartbeat
        self._checkpoint = checkpoint
        self._on_job_committed = on_job_committed
        self._on_job_failed = on_job_failed

    async def run_once(self) -> bool:
        """领取并处理一项任务；没有到期任务时返回 False。"""
        if self._heartbeat is not None:
            self._heartbeat(datetime.now(UTC))
        job = await self._repository.claim_next()
        if job is None:
            return False
        try:
            if self._checkpoint is not None:
                # 先提交 RUNNING 状态，进程中断后才能由超时锁恢复。
                await self._checkpoint()
                # SQLite 只需串行化“读取 PENDING 并提交 RUNNING”的领取阶段；
                # 提交后立即释放，避免慢模型任务阻塞另一个快速发送 worker。
                await self._release_claim_lock()

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
                failed_payload = dict(job.payload)
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
                if self._on_job_failed is not None:
                    await self._on_job_failed(
                        job,
                        type(error).__name__,
                        failed_payload,
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
        finally:
            # 领取提交失败、任务取消或无提交边界时，仍必须可靠释放进程锁。
            await self._release_claim_lock()

    async def _release_claim_lock(self) -> None:
        """按需释放具体仓储提供的 SQLite 领取锁。"""
        release_claim_lock = getattr(
            self._repository,
            "release_claim_lock",
            None,
        )
        if release_claim_lock is not None:
            await release_claim_lock()
