import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from anthropic import AsyncAnthropic
from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.config import Settings
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import BookingApproval, Employee
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.deepseek_client import (
    DeepSeekGuestAssistant,
    HostexReadOnlyToolExecutor,
)
from homestay_bot.integrations.deepseek_context_summarizer import (
    DeepSeekContextSummarizer,
)
from homestay_bot.integrations.deepseek_faq_drafter import DeepSeekFaqDrafter
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.integrations.hostex_client import HostexClient
from homestay_bot.integrations.tourism import WebSearchState
from homestay_bot.integrations.wecom.api_client import (
    WeComApiClient,
    WeComApiError,
)
from homestay_bot.repositories.approvals import (
    SQLAlchemyApprovalRepository,
    SQLAlchemyPermissionChecker,
)
from homestay_bot.repositories.context import SQLAlchemyContextRepository
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.repositories.employees import SQLAlchemyEmployeeRepository
from homestay_bot.repositories.faq_candidates import (
    SQLAlchemyFaqCandidateRepository,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.routes.employee_auth import EmployeeAuthService
from homestay_bot.routes.health import OperationalHealthService
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.routes.wecom_callback import WeComCallbackService
from homestay_bot.services.approval_page_service import ApprovalPageService
from homestay_bot.services.approval_service import ApprovalService
from homestay_bot.services.booking_service import BookingService
from homestay_bot.services.context_retention import ContextRetentionService
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.customer_service import CustomerService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.faq_candidate_context import (
    FaqCandidateContextService,
)
from homestay_bot.services.faq_candidate_service import FrequentFaqService
from homestay_bot.services.faq_draft_job import FaqDraftJobService
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from homestay_bot.services.sensitive_data import SensitiveDataCipher
from homestay_bot.worker import (
    JobHandler,
    RetrySafeJobError,
    WeComMessagePoller,
    WeComSyncJobHandler,
    Worker,
)

logger = logging.getLogger(__name__)


class DurableJobQueue:
    """用短生命周期数据库会话为回调和续页持久化任务。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> None:
        """持久化一项任务并立即提交。"""
        dedupe_key = None
        if job_type == "wecom_sync":
            canonical = json.dumps(
                {
                    "cursor": payload.get("cursor", ""),
                    "token": payload.get("token", ""),
                    "open_kfid": payload.get("open_kfid", ""),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            dedupe_key = "wecom-sync:" + hashlib.sha256(canonical.encode()).hexdigest()
        async with self._factory() as session:
            await SQLAlchemyJobRepository(session).enqueue(
                job_type,
                payload,
                dedupe_key=dedupe_key,
            )
            await session.commit()

    async def enqueue_wecom_sync(self, token: str, open_kfid: str) -> None:
        """把企业微信回调转换为从空游标开始的同步任务。"""
        await self.enqueue(
            "wecom_sync",
            {"cursor": "", "token": token, "open_kfid": open_kfid},
        )


class TransactionalOutboxWeCom:
    """把客人回复和员工通知写入同一数据库事务，避免业务回滚后重复发送。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        source_message_id: str,
    ) -> None:
        """绑定当前消息事务和稳定的来源消息编号。"""
        self._repository = SQLAlchemyJobRepository(session)
        self._source_message_id = source_message_id
        self._sequence = 0

    def _outbox_id(self, kind: str) -> str:
        """为同一入站消息的每项出站动作生成稳定内部编号。"""
        self._sequence += 1
        raw_key = f"{self._source_message_id}:{kind}:{self._sequence}"
        return f"outbox:{hashlib.sha256(raw_key.encode()).hexdigest()}"

    async def send_text(self, open_kfid: str, external_userid: str, content: str) -> str:
        """事务内登记客人回复，真实发送由 worker 在提交后执行。"""
        outbox_id = self._outbox_id("guest")
        await self._repository.enqueue(
            "wecom_send_text",
            {
                "outbox_id": outbox_id,
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "content": content,
            },
            dedupe_key=outbox_id,
        )
        return outbox_id

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """事务内登记员工通知，真实发送由 worker 在提交后执行。"""
        outbox_id = self._outbox_id("internal")
        await self._repository.enqueue(
            "wecom_send_internal_text",
            {
                "outbox_id": outbox_id,
                "agent_id": agent_id,
                "employee_userids": employee_userids,
                "content": content,
            },
            dedupe_key=outbox_id,
        )


class SessionKnowledgeRepository:
    """用独立会话读取当前启用知识，避免长期持有数据库连接。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def list_active(self) -> list[Any]:
        """读取启用知识并在返回前关闭会话。"""
        async with self._factory() as session:
            return await SQLAlchemyKnowledgeRepository(session).list_active()


class SessionFaqCandidateRepository:
    """用独立短会话读取模型可匹配的 FAQ 候选目录。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def list_context(
        self,
        *,
        now: datetime,
    ) -> list[Any]:
        """读取最多五十条开放候选，并提交关闭期满的重开状态。"""
        async with self._factory() as session:
            candidates = await SQLAlchemyFaqCandidateRepository(
                session
            ).list_context(now=now)
            await session.commit()
            return candidates


class SessionEmployeeAuthService:
    """为每次 OAuth 回调创建独立员工查询会话。"""

    def __init__(
        self,
        *,
        corp_id: str,
        public_base_url: str,
        wecom: WeComApiClient,
        factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """保存企业微信客户端和数据库会话工厂。"""
        self._corp_id = corp_id
        self._public_base_url = public_base_url.rstrip("/")
        self._wecom = wecom
        self._factory = factory

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """使用固定公网根地址构造授权链接，不信任请求 Host。"""
        redirect_uri = f"{self._public_base_url}/employee/oauth/callback"
        query = urlencode(
            {
                "appid": self._corp_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
            }
        )
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect"

    async def authenticate(self, code: str) -> Employee:
        """换取 userid，并在独立会话中验证本地启用角色。"""
        async with self._factory() as session:
            service = EmployeeAuthService(
                corp_id=self._corp_id,
                oauth=self._wecom,
                employees=SQLAlchemyEmployeeRepository(session),
            )
            return await service.authenticate(code)


class SessionEmployeeAccessVerifier:
    """每个员工页面请求都从数据库重新确认启用状态和角色。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def get_active(self, employee_id: int) -> Employee | None:
        """在短会话中读取最新员工授权。"""
        async with self._factory() as session:
            return await SQLAlchemyEmployeeRepository(session).get_active(employee_id)


class SessionApprovalPageService:
    """为每次审批读取或确认创建完整且隔离的业务服务。"""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        hostex: HostexClient,
    ) -> None:
        """保存数据库会话工厂和百居易客户端。"""
        self._factory = factory
        self._hostex = hostex

    def _service(self, session: AsyncSession) -> ApprovalPageService:
        """用同一会话组装权限、审批仓储和下单状态机。"""
        booking = BookingService(
            SQLAlchemyApprovalRepository(session),
            SQLAlchemyPermissionChecker(session),
            self._hostex,
        )
        return ApprovalPageService(
            session=session,
            hostex=self._hostex,
            booking=booking,
        )

    async def get_detail(self, approval_id: int) -> dict[str, Any]:
        """在短会话中读取审批页全部数据。"""
        async with self._factory() as session:
            return await self._service(session).get_detail(approval_id)

    async def list_pending(self) -> list[BookingApproval]:
        """在短会话中读取待处理审批列表。"""
        async with self._factory() as session:
            return await self._service(session).list_pending()

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """在独立会话中执行带行锁和幂等保护的确认。"""
        async with self._factory() as session:
            result = await self._service(session).confirm(approval_id, employee_id, command)
            await session.commit()
            return result


class SessionKnowledgeAdminService:
    """为每次管理操作使用独立数据库会话。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def list_all(self) -> list[Any]:
        """返回全部知识条目。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).list_all()

    async def create(self, employee_id: int, **fields: Any) -> Any:
        """创建知识并由底层服务提交审计。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).create(employee_id, **fields)

    async def update(self, entry_id: int, employee_id: int, **fields: Any) -> Any:
        """更新知识并由底层服务提交审计。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).update(entry_id, employee_id, **fields)

    async def set_enabled(self, entry_id: int, employee_id: int, enabled: bool) -> None:
        """切换知识启用状态。"""
        async with self._factory() as session:
            await KnowledgeAdminService(session).set_enabled(entry_id, employee_id, enabled)

    async def list_candidates(self) -> list[Any]:
        """返回管理员可审核的高频 FAQ 候选。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).list_candidates()

    async def convert_candidate(
        self,
        candidate_id: int,
        employee_id: int,
        **fields: Any,
    ) -> Any:
        """在独立会话中把管理员修改后的候选转为正式知识。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).convert_candidate(
                candidate_id,
                employee_id,
                **fields,
            )

    async def snooze_candidate(
        self,
        candidate_id: int,
        employee_id: int,
    ) -> None:
        """在独立会话中关闭候选三十天。"""
        async with self._factory() as session:
            await KnowledgeAdminService(session).snooze_candidate(
                candidate_id,
                employee_id,
            )


def _register_faq_draft_handler(
    handlers: dict[str, JobHandler],
    session: AsyncSession,
    factory: Callable[[AsyncSession], JobHandler] | None,
) -> None:
    """为当前 worker 事务按需注册 FAQ 草稿处理器。"""
    if factory is not None:
        handlers["faq_draft_generate"] = factory(session)


async def _run_worker_loop(
    app: FastAPI,
    *,
    factory: async_sessionmaker[AsyncSession],
    handler: WeComSyncJobHandler,
    wecom: WeComApiClient,
    faq_draft_handler_factory: (
        Callable[[AsyncSession], JobHandler] | None
    ) = None,
) -> None:
    """持续处理持久化任务，并周期恢复五分钟前的遗留锁。"""
    while True:
        try:
            async with factory() as session:
                repository = SQLAlchemyJobRepository(session)
                await SQLAlchemyApprovalRepository(session).recover_stale_creating(
                    before=datetime.now(UTC) - timedelta(minutes=5)
                )
                await repository.recover_stale(before=datetime.now(UTC) - timedelta(minutes=5))
                await session.commit()

                async def send_guest(payload: dict[str, Any]) -> None:
                    """发送客人回复并回写真实 msgid；只重试确定未发送的错误。"""
                    try:
                        real_message_id = await wecom.send_text(
                            str(payload["open_kfid"]),
                            str(payload["external_userid"]),
                            str(payload["content"]),
                        )
                    except httpx.ConnectError as error:
                        raise RetrySafeJobError("企业微信连接尚未建立") from error
                    except WeComApiError as error:
                        if error.error_code == 45009:
                            raise RetrySafeJobError("企业微信明确限流") from error
                        raise
                    await SQLAlchemyMessageRepository(session).replace_external_message_id(
                        str(payload["outbox_id"]),
                        real_message_id,
                    )

                async def send_internal(payload: dict[str, Any]) -> None:
                    """发送员工通知；连接失败或明确限流时才允许有限重试。"""
                    try:
                        await wecom.send_internal_text(
                            agent_id=int(payload["agent_id"]),
                            employee_userids=list(payload["employee_userids"]),
                            content=str(payload["content"]),
                        )
                    except httpx.ConnectError as error:
                        raise RetrySafeJobError("企业微信连接尚未建立") from error
                    except WeComApiError as error:
                        if error.error_code == 45009:
                            raise RetrySafeJobError("企业微信明确限流") from error
                        raise

                handlers: dict[str, JobHandler] = {
                    "wecom_sync": handler,
                    "wecom_send_text": send_guest,
                    "wecom_send_internal_text": send_internal,
                }
                _register_faq_draft_handler(
                    handlers,
                    session,
                    faq_draft_handler_factory,
                )
                worker = Worker(
                    repository=repository,
                    handlers=handlers,
                    heartbeat=lambda value: setattr(app.state, "worker_last_heartbeat", value),
                    checkpoint=session.commit,
                )
                handled = await worker.run_once()
        except OperationalError as error:
            if "database is locked" not in str(error).lower():
                raise
            # SQLite 本地测试发生瞬时写锁时保活 worker，稍后继续处理队列。
            logger.warning("后台任务遇到 SQLite 写锁，1 秒后重试")
            await asyncio.sleep(1)
            continue
        if not handled:
            await asyncio.sleep(1)


async def _run_faq_maintenance_loop(
    *,
    factory: async_sessionmaker[AsyncSession],
    now_provider: Callable[[], datetime] | None = None,
) -> None:
    """每小时清理过期明细并重开已结束关闭期的候选。"""
    current_time = now_provider or (lambda: datetime.now(UTC))
    while True:
        try:
            async with factory() as session:
                await SQLAlchemyFaqCandidateRepository(session).maintain(
                    now=current_time()
                )
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # 周期维护失败不影响消息 worker，只记录异常类型等待下轮重试。
            logger.warning(
                "FAQ 周期维护失败：error_type=%s",
                type(error).__name__,
            )
        await asyncio.sleep(3600)


async def _run_context_maintenance_loop(
    *,
    factory: async_sessionmaker[AsyncSession],
    summarizer: Any,
    now_provider: Callable[[], datetime] | None = None,
) -> None:
    """每小时为有消息的正式客户更新分层摘要。"""
    current_time = now_provider or (lambda: datetime.now(UTC))
    while True:
        try:
            async with factory() as session:
                repository = SQLAlchemyContextRepository(session)
                customer_ids = await repository.list_customer_ids_with_messages()
                service = ContextRetentionService(repository, summarizer)
                cycle_now = current_time()
                for customer_id in customer_ids:
                    await service.maintain_customer(customer_id, cycle_now)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # 摘要失败时事务回滚并保留原文，下一周期自动重试。
            logger.warning(
                "客户上下文维护失败：error_type=%s",
                type(error).__name__,
            )
        await asyncio.sleep(3600)


def _next_wecom_poll_delay(
    *,
    current_delay: float,
    interval_seconds: float,
    error: Exception,
) -> float:
    """按错误类型计算补拉退避时间，并把等待上限控制在五分钟。"""
    minimum_delay = (
        60.0 if isinstance(error, WeComApiError) and error.error_code == 45009 else interval_seconds
    )
    return min(max(current_delay * 2, minimum_delay), 300.0)


async def _run_wecom_poll_loop(
    app: FastAPI,
    *,
    poller: WeComMessagePoller,
    interval_seconds: float,
) -> None:
    """周期补拉客服消息；失败时退避，成功时更新健康心跳。"""
    delay = interval_seconds
    while True:
        await asyncio.sleep(delay)
        try:
            await poller.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            delay = _next_wecom_poll_delay(
                current_delay=delay,
                interval_seconds=interval_seconds,
                error=error,
            )
            # 只记录异常类型，避免企业微信错误正文携带请求细节。
            logger.warning(
                "企业微信定时补拉失败，%s 秒后重试：%s",
                delay,
                type(error).__name__,
            )
        else:
            app.state.wecom_poll_last_success = datetime.now(UTC)
            delay = interval_seconds


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """在配置完整时装配外部客户端、数据库服务和后台 worker。"""
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError:
        # 未配置时仍允许启动健康页，便于本地发现缺失项。
        yield
        return

    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    hostex = HostexClient(settings.hostex_access_token)
    wecom = WeComApiClient(
        settings.wecom_corp_id,
        settings.wecom_kf_secret,
        settings.wecom_agent_secret,
    )
    deepseek_chat = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    deepseek_anthropic = AsyncAnthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
    )
    queue = DurableJobQueue(factory)
    knowledge = KnowledgeService(SessionKnowledgeRepository(factory))
    faq_candidate_context = FaqCandidateContextService(
        SessionFaqCandidateRepository(factory)
    )
    web_search_state = WebSearchState()
    tourism_searcher = DeepSeekTourismSearcher(
        client=deepseek_anthropic,
        model=settings.deepseek_model,
        status_setter=web_search_state.set,
    )
    assistant = DeepSeekGuestAssistant(
        chat_client=deepseek_chat,
        tourism_searcher=tourism_searcher,
        knowledge=knowledge,
        model=settings.deepseek_model,
        safety_hmac_key=settings.session_secret.encode(),
        tool_executor=HostexReadOnlyToolExecutor(hostex),
        faq_candidate_context=faq_candidate_context,
    )
    faq_drafter = DeepSeekFaqDrafter(
        client=deepseek_chat,
        model=settings.deepseek_model,
    )
    context_summarizer = DeepSeekContextSummarizer(
        deepseek_chat,
        settings.deepseek_model,
    )
    duty_userids = [item.strip() for item in settings.wecom_duty_userids.split(",") if item.strip()]
    sensitive_data = SensitiveDataCipher(settings.data_encryption_key)

    async def handle_message(message: IncomingMessage) -> None:
        """在独立事务中处理一条已转换的企业微信消息。"""
        async with factory() as session:
            faq_candidates = SQLAlchemyFaqCandidateRepository(session)
            service = ConversationService(
                conversations=SQLAlchemyConversationRepository(session),
                messages=MessageService(SQLAlchemyMessageRepository(session)),
                assistant=assistant,
                emergency_service=EmergencyService(),
                wecom=TransactionalOutboxWeCom(
                    session,
                    source_message_id=message.msgid,
                ),
                agent_id=settings.wecom_agent_id,
                duty_employee_userids=duty_userids,
                approvals=ApprovalService(SQLAlchemyApprovalRepository(session)),
                approval_base_url=settings.public_base_url,
                frequent_faq=FrequentFaqService(
                    candidates=faq_candidates,
                    jobs=SQLAlchemyJobRepository(session),
                    savepoint_factory=session.begin_nested,
                ),
                customer_profiles=CustomerService(
                    SQLAlchemyCustomerRepository(session),
                    sensitive_data,
                ),
                customer_context=SQLAlchemyContextRepository(session),
            )
            await service.handle_message(message)
            await session.commit()

    sync_handler = WeComSyncJobHandler(
        api=wecom,
        handle_message=handle_message,
        enqueue=queue.enqueue,
    )
    poller = WeComMessagePoller(api=wecom, handler=sync_handler)

    def build_faq_draft_handler(session: AsyncSession) -> JobHandler:
        """为当前 worker 会话创建可原子保存草稿和通知的处理器。"""

        async def handle_faq_draft(payload: dict[str, Any]) -> None:
            """按候选代次生成草稿，并把管理员通知写入同一事务。"""
            candidate_id = int(payload["candidate_id"])
            generation = int(payload["generation"])
            service = FaqDraftJobService(
                candidates=SQLAlchemyFaqCandidateRepository(session),
                drafter=faq_drafter,
                knowledge=KnowledgeService(
                    cast(
                        Any,
                        SQLAlchemyKnowledgeRepository(session),
                    )
                ),
                administrators=SQLAlchemyEmployeeRepository(session),
                notifications=TransactionalOutboxWeCom(
                    session,
                    source_message_id=(
                        f"faq-draft:{candidate_id}:{generation}"
                    ),
                ),
                agent_id=settings.wecom_agent_id,
                knowledge_admin_url=(
                    f"{settings.public_base_url.rstrip('/')}"
                    "/employee/knowledge"
                ),
            )
            await service.handle(payload)

        return handle_faq_draft

    async def database_probe() -> bool:
        """执行无副作用 SELECT 1 检查数据库连接。"""
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    app.state.employee_auth_service = SessionEmployeeAuthService(
        corp_id=settings.wecom_corp_id,
        public_base_url=settings.public_base_url,
        wecom=wecom,
        factory=factory,
    )
    app.state.employee_access_verifier = SessionEmployeeAccessVerifier(factory)
    app.state.approval_page_service = SessionApprovalPageService(
        factory=factory,
        hostex=hostex,
    )
    app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory)
    app.state.wecom_callback_service = WeComCallbackService.from_credentials(
        settings.wecom_callback_token,
        settings.wecom_encoding_aes_key,
        settings.wecom_corp_id,
        queue,
    )
    app.state.worker_last_heartbeat = datetime.now(UTC)
    # 启动宽限期避免首次补拉前被误报；一次成功后由真实心跳覆盖。
    app.state.wecom_poll_last_success = datetime.now(UTC)
    app.state.health_service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: app.state.worker_last_heartbeat,
        poll_heartbeat_getter=lambda: app.state.wecom_poll_last_success,
        configuration_ok=bool(duty_userids),
        web_search_status_getter=web_search_state.get,
        poll_max_age=timedelta(
            seconds=max(
                60,
                settings.wecom_poll_interval_seconds * 3,
            )
        ),
    )
    worker_task = asyncio.create_task(
        _run_worker_loop(
            app,
            factory=factory,
            handler=sync_handler,
            wecom=wecom,
            faq_draft_handler_factory=build_faq_draft_handler,
        )
    )
    poll_task = asyncio.create_task(
        _run_wecom_poll_loop(
            app,
            poller=poller,
            interval_seconds=settings.wecom_poll_interval_seconds,
        )
    )
    faq_maintenance_task = asyncio.create_task(
        _run_faq_maintenance_loop(factory=factory)
    )
    context_maintenance_task = asyncio.create_task(
        _run_context_maintenance_loop(
            factory=factory,
            summarizer=context_summarizer,
        )
    )

    try:
        yield
    finally:
        background_tasks = (
            worker_task,
            poll_task,
            faq_maintenance_task,
            context_maintenance_task,
        )
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # 测试重启或同进程重新装配时不得沿用已关闭的客户端与会话服务。
        for state_name in (
            "employee_auth_service",
            "employee_access_verifier",
            "approval_page_service",
            "knowledge_admin_service",
            "wecom_callback_service",
            "health_service",
            "worker_last_heartbeat",
            "wecom_poll_last_success",
        ):
            if hasattr(app.state, state_name):
                delattr(app.state, state_name)
        await deepseek_chat.close()
        await deepseek_anthropic.close()
        await hostex.aclose()
        await wecom.aclose()
        await engine.dispose()
