import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.config import Settings
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import BookingApproval, Employee
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import HostexClient
from homestay_bot.integrations.openai_client import (
    GuestAssistant,
    HostexReadOnlyToolExecutor,
)
from homestay_bot.integrations.wecom.api_client import WeComApiClient
from homestay_bot.repositories.approvals import (
    SQLAlchemyApprovalRepository,
    SQLAlchemyPermissionChecker,
)
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.employees import SQLAlchemyEmployeeRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.routes.employee_auth import EmployeeAuthService
from homestay_bot.routes.health import OperationalHealthService
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.routes.wecom_callback import WeComCallbackService
from homestay_bot.services.approval_page_service import ApprovalPageService
from homestay_bot.services.booking_service import BookingService
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from homestay_bot.worker import WeComSyncJobHandler, Worker


class DurableJobQueue:
    """用短生命周期数据库会话为回调和续页持久化任务。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def enqueue(
        self, job_type: str, payload: dict[str, Any]
    ) -> None:
        """持久化一项任务并立即提交。"""
        async with self._factory() as session:
            await SQLAlchemyJobRepository(session).enqueue(job_type, payload)
            await session.commit()

    async def enqueue_wecom_sync(
        self, token: str, open_kfid: str
    ) -> None:
        """把企业微信回调转换为从空游标开始的同步任务。"""
        await self.enqueue(
            "wecom_sync",
            {"cursor": "", "token": token, "open_kfid": open_kfid},
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


class SessionEmployeeAuthService:
    """为每次 OAuth 回调创建独立员工查询会话。"""

    def __init__(
        self,
        *,
        corp_id: str,
        wecom: WeComApiClient,
        factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """保存企业微信客户端和数据库会话工厂。"""
        self._corp_id = corp_id
        self._wecom = wecom
        self._factory = factory

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """构造企业微信成员网页授权地址。"""
        query = urlencode(
            {
                "appid": self._corp_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
            }
        )
        return (
            "https://open.weixin.qq.com/connect/oauth2/authorize?"
            f"{query}#wechat_redirect"
        )

    async def authenticate(self, code: str) -> Employee:
        """换取 userid，并在独立会话中验证本地启用角色。"""
        async with self._factory() as session:
            service = EmployeeAuthService(
                corp_id=self._corp_id,
                oauth=self._wecom,
                employees=SQLAlchemyEmployeeRepository(session),
            )
            return await service.authenticate(code)


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

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """在独立会话中执行带行锁和幂等保护的确认。"""
        async with self._factory() as session:
            result = await self._service(session).confirm(
                approval_id, employee_id, command
            )
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
            return await KnowledgeAdminService(session).create(
                employee_id, **fields
            )

    async def update(
        self, entry_id: int, employee_id: int, **fields: Any
    ) -> Any:
        """更新知识并由底层服务提交审计。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).update(
                entry_id, employee_id, **fields
            )

    async def set_enabled(
        self, entry_id: int, employee_id: int, enabled: bool
    ) -> None:
        """切换知识启用状态。"""
        async with self._factory() as session:
            await KnowledgeAdminService(session).set_enabled(
                entry_id, employee_id, enabled
            )


async def _run_worker_loop(
    app: FastAPI,
    *,
    factory: async_sessionmaker[AsyncSession],
    handler: WeComSyncJobHandler,
) -> None:
    """持续处理持久化任务，并周期恢复五分钟前的遗留锁。"""
    while True:
        async with factory() as session:
            repository = SQLAlchemyJobRepository(session)
            await repository.recover_stale(
                before=datetime.now(UTC) - timedelta(minutes=5)
            )
            await session.commit()
            worker = Worker(
                repository=repository,
                handlers={"wecom_sync": handler},
                heartbeat=lambda value: setattr(
                    app.state, "worker_last_heartbeat", value
                ),
                checkpoint=session.commit,
            )
            handled = await worker.run_once()
        if not handled:
            await asyncio.sleep(1)


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
    openai = AsyncOpenAI(api_key=settings.openai_api_key)
    queue = DurableJobQueue(factory)
    knowledge = KnowledgeService(SessionKnowledgeRepository(factory))
    assistant = GuestAssistant(
        client=openai,
        knowledge=knowledge,
        model=settings.openai_model,
        safety_hmac_key=settings.session_secret.encode(),
        tool_executor=HostexReadOnlyToolExecutor(hostex),
    )
    duty_userids = [
        item.strip()
        for item in settings.wecom_duty_userids.split(",")
        if item.strip()
    ]

    async def handle_message(message: IncomingMessage) -> None:
        """在独立事务中处理一条已转换的企业微信消息。"""
        async with factory() as session:
            service = ConversationService(
                conversations=SQLAlchemyConversationRepository(session),
                messages=MessageService(SQLAlchemyMessageRepository(session)),
                assistant=assistant,
                emergency_service=EmergencyService(),
                wecom=wecom,
                agent_id=settings.wecom_agent_id,
                duty_employee_userids=duty_userids,
            )
            await service.handle_message(message)
            await session.commit()

    sync_handler = WeComSyncJobHandler(
        api=wecom,
        handle_message=handle_message,
        enqueue=queue.enqueue,
    )

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
        wecom=wecom,
        factory=factory,
    )
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
    app.state.health_service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: app.state.worker_last_heartbeat,
        configuration_ok=bool(duty_userids),
    )
    worker_task = asyncio.create_task(
        _run_worker_loop(app, factory=factory, handler=sync_handler)
    )

    try:
        yield
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        # 测试重启或同进程重新装配时不得沿用已关闭的客户端与会话服务。
        for state_name in (
            "employee_auth_service",
            "approval_page_service",
            "knowledge_admin_service",
            "wecom_callback_service",
            "health_service",
            "worker_last_heartbeat",
        ):
            if hasattr(app.state, state_name):
                delattr(app.state, state_name)
        await openai.close()
        await hostex.aclose()
        await wecom.aclose()
        await engine.dispose()
