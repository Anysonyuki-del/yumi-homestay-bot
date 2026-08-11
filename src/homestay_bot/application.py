import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, BinaryIO, cast

import httpx
from cryptography.fernet import InvalidToken
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.config import BootstrapSettings, RuntimeEnvironmentSettings
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import ComplaintReviewStatus, EmployeeRole, MessageOrigin
from homestay_bot.domain.models import (
    AdminCredential,
    BookingApproval,
    Conversation,
    Employee,
    Message,
)
from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot, RuntimeConfigView
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import HostexClient
from homestay_bot.integrations.tourism import WebSearchState
from homestay_bot.integrations.wecom.api_client import (
    WeComApiClient,
    WeComApiError,
)
from homestay_bot.repositories.admin_credentials import (
    SQLAlchemyAdminCredentialRepository,
)
from homestay_bot.repositories.admin_csrf import SQLAlchemyAdminCsrfRepository
from homestay_bot.repositories.approvals import (
    SQLAlchemyApprovalRepository,
    SQLAlchemyPermissionChecker,
)
from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository
from homestay_bot.repositories.context import SQLAlchemyContextRepository
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.credentials import (
    SQLAlchemyCredentialDeliveryRepository,
)
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.repositories.employees import SQLAlchemyEmployeeRepository
from homestay_bot.repositories.faq_candidates import (
    SQLAlchemyFaqCandidateRepository,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.repositories.lifecycle_reminders import (
    SQLAlchemyLifecycleReminderRepository,
)
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.repositories.retention import SQLAlchemyRetentionRepository
from homestay_bot.repositories.runtime_config import (
    RuntimeConfigConflictError,
    SQLAlchemyRuntimeConfigRepository,
)
from homestay_bot.routes.employee_auth import AdminLoginRateLimiter
from homestay_bot.routes.health import OperationalHealthService
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.services.admin_auth_service import (
    AdminAuthService,
    AdminSession,
    AuthenticationError,
    PasswordHasherPort,
)
from homestay_bot.services.admin_csrf import AdminCsrfService
from homestay_bot.services.admin_dashboard_service import AdminDashboardService, Snapshot
from homestay_bot.services.admin_passwords import (
    ADMIN_PASSWORD_HASHER,
    validate_admin_password_hash,
)
from homestay_bot.services.approval_page_service import ApprovalPageService
from homestay_bot.services.approval_service import ApprovalService
from homestay_bot.services.booking_service import BookingService
from homestay_bot.services.business_task_service import BusinessTaskService
from homestay_bot.services.cancellation import complete_cleanup
from homestay_bot.services.complaint_admin_service import ComplaintAdminService
from homestay_bot.services.complaint_review_job import (
    ComplaintReviewJobService,
    SQLAlchemyComplaintMessageContext,
)
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.context_retention import ContextRetentionService
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.credential_delivery import (
    CredentialDeliveryService,
    CredentialPartSender,
)
from homestay_bot.services.customer_admin_service import CustomerAdminService
from homestay_bot.services.customer_service import CustomerService
from homestay_bot.services.customer_tag_sync import CustomerTagSyncService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.faq_candidate_context import (
    FaqCandidateContextService,
)
from homestay_bot.services.faq_candidate_service import FrequentFaqService
from homestay_bot.services.faq_draft_job import FaqDraftJobService
from homestay_bot.services.hostex_sync import HostexSyncService
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.services.lifecycle_reminders import (
    LifecycleReminderService,
)
from homestay_bot.services.message_service import IncomingMessage, MessageService
from homestay_bot.services.private_file_storage import (
    PrivateFileStorage,
    StoredPrivateFile,
)
from homestay_bot.services.property_admin_service import (
    PropertyAdminService,
    PropertyFields,
)
from homestay_bot.services.room_readiness_service import RoomReadinessService
from homestay_bot.services.runtime_clients import (
    RuntimeClientBundle,
    RuntimeClientRegistry,
    build_runtime_client_bundle,
)
from homestay_bot.services.runtime_config_cipher import (
    RuntimeConfigCipher,
    RuntimeConfigPayloadError,
)
from homestay_bot.services.runtime_config_service import (
    ActivationResult,
    RuntimeConfigCompensationConflictError,
    RuntimeConfigPage,
    RuntimeConfigService,
    RuntimeConfigTesterPort,
    RuntimeConfigTestError,
    RuntimeConfigTestResult,
    RuntimeConfigUnavailableError,
    RuntimeConfigVersionView,
    UpdateRuntimeConfig,
    safe_provider_results,
)
from homestay_bot.services.runtime_config_tester import RuntimeConfigTester
from homestay_bot.services.sensitive_data import SensitiveDataCipher
from homestay_bot.services.task_page_service import TaskPageService
from homestay_bot.worker import (
    JobHandler,
    RetrySafeJobError,
    WeComMessagePoller,
    WeComSyncJobHandler,
    Worker,
)

logger = logging.getLogger(__name__)


def _create_runtime_task(
    coroutine: Coroutine[Any, Any, None],
) -> asyncio.Task[None]:
    """集中创建运行后台task，便于协调器验证中途失败的清理语义。"""
    return asyncio.create_task(coroutine)


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


class SessionHostexEventRecorder:
    """用短会话原子保存百居易事件和后台任务。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def record_hostex_event(self, **fields: Any) -> bool:
        """在同一事务写入事件与唯一任务并立即提交。"""
        async with self._factory() as session:
            created = await SQLAlchemyOperationsRepository(session).record_hostex_event(**fields)
            await session.commit()
            return created


class TransactionalOutboxWeCom:
    """把客人回复和员工通知写入同一数据库事务，避免业务回滚后重复发送。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        source_message_id: str,
        delivery_phase: str | None = None,
    ) -> None:
        """绑定来源消息及可选发送阶段，确保分阶段回复分别保持幂等。"""
        self._repository = SQLAlchemyJobRepository(session)
        self._source_message_id = (
            f"{source_message_id}:{delivery_phase}" if delivery_phase else source_message_id
        )
        self._sequence = 0

    def _outbox_id(self, kind: str) -> str:
        """为同一入站消息的每项出站动作生成稳定内部编号。"""
        self._sequence += 1
        raw_key = f"{self._source_message_id}:{kind}:{self._sequence}"
        return f"outbox:{hashlib.sha256(raw_key.encode()).hexdigest()}"

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
        *,
        message_type: str = "text",
        delivery_retry_count: int = 0,
        retry_of_message_id: str | None = None,
    ) -> str | None:
        """事务内登记客人回复，真实发送由 worker 在提交后执行。"""
        outbox_id = self._outbox_id("guest")
        if await self._repository.exists_dedupe_key(outbox_id):
            return None
        await self._repository.enqueue(
            "wecom_send_text",
            {
                "outbox_id": outbox_id,
                "source_message_id": self._source_message_id,
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "content": content,
                "message_type": message_type,
                "delivery_retry_count": delivery_retry_count,
                "retry_of_message_id": retry_of_message_id,
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

    async def send_internal_card(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        title: str,
        description: str,
        url: str,
    ) -> None:
        """事务内登记员工后台入口卡片。"""
        outbox_id = self._outbox_id("internal-card")
        await self._repository.enqueue(
            "wecom_send_internal_card",
            {
                "outbox_id": outbox_id,
                "agent_id": agent_id,
                "employee_userids": employee_userids,
                "title": title,
                "description": description,
                "url": url,
            },
            dedupe_key=outbox_id,
        )


async def _record_complaint_delivery(
    session: AsyncSession,
    source_message_id: str,
    *,
    delivered: bool,
    error_code: str | None = None,
    external_message_id: str | None = None,
) -> None:
    """按事务型 outbox 来源回写客诉真实投递结果。"""
    if not source_message_id.startswith("complaint:"):
        return
    # 客诉重试会在编号后追加阶段（例如 complaint:17:retry-2），
    # 回写时只取稳定的数字主键，不能因为阶段后缀丢失状态更新。
    review_token = source_message_id.removeprefix("complaint:").split(":", 1)[0]
    if not review_token.isdecimal():
        return
    review_id = int(review_token)
    repository = SQLAlchemyComplaintRepository(session)
    if delivered:
        if not external_message_id:
            return
        await repository.mark_delivery_sent(
            review_id,
            sent_at=datetime.now(UTC),
            external_message_id=external_message_id,
        )
    else:
        await repository.mark_delivery_failed(
            review_id,
            error_code=error_code or "unknown_delivery_error",
        )


async def _handle_guest_delivery_failure(
    session: AsyncSession,
    external_message_id: str,
    *,
    fail_type: int,
) -> bool:
    """记录普通机器人消息失败，并为其登记一次去重重试。"""
    repository = SQLAlchemyMessageRepository(session)
    message = await repository.mark_delivery_failed(
        external_message_id,
        error_code=f"wecom_async_{fail_type}",
    )
    if message is None or message.origin is not MessageOrigin.BOT or not message.content:
        return False
    metadata = dict(message.message_metadata or {})
    try:
        retry_count = int(metadata.get("delivery_retry_count", 0))
    except (TypeError, ValueError):
        retry_count = 0
    if retry_count >= 1:
        metadata["delivery_retry_pending"] = False
        message.message_metadata = metadata
        await session.flush()
        return False
    conversation = await session.get(Conversation, message.conversation_id)
    if conversation is None:
        return False
    # 企业微信的安全限制通常针对正文内容；原文再次发送只会重复失败，
    # 因此改发不含外链、地址和敏感细节的短消息，先保证客人收到回应。
    retry_content = (
        "我已收到您的问题，正在为您核实相关信息，请稍等片刻。"
        if fail_type == 13
        else message.content
    )
    outbox = TransactionalOutboxWeCom(
        session,
        source_message_id=f"delivery-retry:{message.id}",
        delivery_phase="guest",
    )
    outbox_id = await outbox.send_text(
        conversation.open_kfid,
        conversation.external_userid,
        retry_content,
        delivery_retry_count=retry_count + 1,
        retry_of_message_id=str(message.id),
    )
    if outbox_id is None:
        return False
    metadata["delivery_retry_count"] = retry_count + 1
    metadata["delivery_retry_outbox_id"] = outbox_id
    metadata["delivery_retry_pending"] = True
    if fail_type == 13:
        metadata["delivery_fallback_used"] = True
    message.message_metadata = metadata
    await session.flush()
    return True


async def _notify_guest_delivery_failure(
    session: AsyncSession,
    external_message_id: str,
    *,
    agent_id: int,
    employee_userids: list[str],
) -> bool:
    """重试仍失败时登记一次脱敏人工跟进通知。"""
    if not employee_userids:
        return False
    message = await session.scalar(
        select(Message).where(Message.external_message_id == external_message_id)
    )
    if message is None or message.origin is not MessageOrigin.BOT:
        return False
    metadata = dict(message.message_metadata or {})
    if metadata.get("delivery_retry_pending") or metadata.get("delivery_failure_notified"):
        return False
    outbox = TransactionalOutboxWeCom(
        session,
        source_message_id=f"delivery-alert:{message.id}",
        delivery_phase="guest",
    )
    await outbox.send_internal_text(
        agent_id=agent_id,
        employee_userids=employee_userids,
        content="有一条客人消息未成功送达，请管家人工跟进。",
    )
    metadata["delivery_failure_notified"] = True
    message.message_metadata = metadata
    await session.flush()
    return True


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
            candidates = await SQLAlchemyFaqCandidateRepository(session).list_context(now=now)
            await session.commit()
            return candidates


class SessionAdminAuthService:
    """为每个管理员认证动作创建独立短事务。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        password_hasher: PasswordHasherPort,
        dummy_hash: str,
        argon2_semaphore: asyncio.Semaphore,
        argon2_executor: ThreadPoolExecutor,
    ) -> None:
        """保存会话工厂及应用生命周期共享的 Argon2 组件。"""
        self._factory = factory
        self._password_hasher = password_hasher
        self._dummy_hash = dummy_hash
        self._argon2_semaphore = argon2_semaphore
        self._argon2_executor = argon2_executor

    def _service(self, session: AsyncSession) -> AdminAuthService:
        """为当前事务组装唯一管理员认证服务。"""
        return AdminAuthService(
            SQLAlchemyAdminCredentialRepository(session),
            password_hasher=self._password_hasher,
            dummy_hash=self._dummy_hash,
            argon2_semaphore=self._argon2_semaphore,
            argon2_executor=self._argon2_executor,
        )

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """认证并提交成功状态或失败计数，不记录敏感参数。"""
        async with self._factory() as session:
            try:
                authenticated = await self._service(session).authenticate(
                    username,
                    password,
                    now,
                )
            except AuthenticationError:
                await session.commit()
                raise
            await session.commit()
            return authenticated

    async def change_password(self, admin_id: int, current: str, new: str) -> None:
        """在独立事务原子改密并提交会话版本。"""
        async with self._factory() as session:
            try:
                await self._service(session).change_password(admin_id, current, new)
            except (AuthenticationError, ValueError):
                await session.commit()
                raise
            await session.commit()

    async def reverify(self, admin_id: int, password: str) -> None:
        """在独立事务复核高风险操作密码并提交失败计数。"""
        async with self._factory() as session:
            try:
                await self._service(session).reverify(admin_id, password)
            except AuthenticationError:
                await session.commit()
                raise
            await session.commit()

    async def revoke_other_sessions(self, admin_id: int) -> int:
        """原子递增会话版本并提交，返回当前浏览器应采用的新版本。"""
        async with self._factory() as session:
            version = await self._service(session).revoke_other_sessions(admin_id)
            await session.commit()
            return version

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> int:
        """在单一数据库事务内复核密码并 CAS 撤销其他会话。"""
        async with self._factory() as session:
            try:
                version = await self._service(session).reverify_and_revoke_sessions(
                    admin_id,
                    password,
                    expected_session_version,
                )
            except AuthenticationError:
                await session.commit()
                raise
            await session.commit()
            return version


class LocalRuntimeConfigTester:
    """批次四只做本地结构校验，真实外联探针由后续批次替换。"""

    async def test(self, snapshot: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
        """验证完整快照边界，不发送消息、不创建订单也不访问网络。"""
        try:
            snapshot.validate()
        except ValueError:
            return RuntimeConfigTestResult(
                succeeded=False,
                error_code="runtime_config_invalid",
            )
        return RuntimeConfigTestResult(succeeded=True)


class UnavailableAdminReverify:
    """在管理员认证未装配时拒绝所有运行配置写操作。"""

    async def reverify_at_version(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> None:
        """显式报告服务不可写，绝不伪造密码复核成功。"""
        raise RuntimeConfigUnavailableError("管理员认证不可用，运行配置只读")


class EnvironmentFallbackRuntimeConfigRepository:
    """保留真实 CAS 状态，同时让损坏 active 以环境快照作为修复基线。"""

    def __init__(
        self,
        repository: SQLAlchemyRuntimeConfigRepository,
        *,
        previous_version_id: int | None,
    ) -> None:
        """保存真实仓储及已确认可解密的上一版本编号。"""
        self._repository = repository
        self._previous_version_id = previous_version_id

    def __getattr__(self, name: str) -> Any:
        """把候选、激活、审计和清理操作委托给真实仓储。"""
        return getattr(self._repository, name)

    async def get_active_version(self) -> None:
        """屏蔽已确认损坏的 active 密文，避免设置页再次解密失败。"""
        return None

    async def get_activation_context(self) -> tuple[Any, None]:
        """返回真实 revision 和指针，但以空版本触发完整环境修复基线。"""
        state, _ = await self._repository.get_activation_context()
        return state, None

    async def activate(self, version_id: int, expected_revision: int) -> Any:
        """修复激活时禁止把已损坏的 active 变成可回滚版本。"""
        return await self._repository.activate_repair(
            version_id,
            expected_revision,
            previous_version_id=self._previous_version_id,
        )


class SessionRuntimeConfigService:
    """为每次配置操作创建短会话，并保持外联测试不占用数据库事务。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        cipher: RuntimeConfigCipher | None,
        environment_snapshot: RuntimeConfigSnapshot | None,
        password_hasher: PasswordHasherPort,
        dummy_hash: str | None,
        argon2_semaphore: asyncio.Semaphore | None,
        argon2_executor: ThreadPoolExecutor | None,
        writable: bool,
        tester: RuntimeConfigTesterPort | None = None,
        registry: RuntimeClientRegistry | None = None,
        bundle_builder: (
            Callable[[RuntimeConfigSnapshot, int], Any] | None
        ) = None,
        runtime_consistency_setter: Callable[[bool], None] | None = None,
        runtime_activator: (
            Callable[[RuntimeConfigSnapshot, int], Any] | None
        ) = None,
    ) -> None:
        """固定环境快照与测试端口；默认本地 stub 防止测试意外联网。"""
        self._factory = factory
        self._cipher = cipher
        self._environment_snapshot = environment_snapshot
        self._password_hasher = password_hasher
        self._dummy_hash = dummy_hash
        self._argon2_semaphore = argon2_semaphore
        self._argon2_executor = argon2_executor
        self._writable = writable
        self._tester = tester or LocalRuntimeConfigTester()
        self._registry = registry
        self._bundle_builder = bundle_builder
        self._runtime_consistency_setter = runtime_consistency_setter
        self._runtime_activator = runtime_activator
        self._activation_lock = asyncio.Lock()
        self._closing = False

    def begin_closing(self) -> None:
        """同步禁止新激活操作进入外联或数据库临界区。"""
        self._closing = True

    async def wait_for_activation_idle(self) -> None:
        """取消安全地等待当前认证、测试、发布及补偿全部退出。"""

        async def wait_for_lock() -> None:
            """以同一activation锁作为完整操作的生命周期屏障。"""
            async with self._activation_lock:
                pass

        await complete_cleanup(wait_for_lock())

    def configure_runtime_activation(
        self,
        registry: RuntimeClientRegistry | None = None,
        bundle_builder: Callable[[RuntimeConfigSnapshot, int], Any] | None = None,
        runtime_consistency_setter: Callable[[bool], None] | None = None,
        *,
        runtime_activator: (
            Callable[[RuntimeConfigSnapshot, int], Any] | None
        ) = None,
    ) -> None:
        """接入可同时处理首次启动与后续swap的运行时协调入口。"""
        self._registry = registry
        self._bundle_builder = bundle_builder
        self._runtime_consistency_setter = runtime_consistency_setter
        self._runtime_activator = runtime_activator

    async def _mark_runtime_consistent_if_current(self, revision: int) -> None:
        """仅当registry已发布同一DB revision时恢复健康标志。"""
        if self._runtime_consistency_setter is None:
            return
        if self._registry is None:
            self._runtime_consistency_setter(False)
            return
        async with self._registry.acquire() as bundle:
            self._runtime_consistency_setter(bundle.revision == revision)

    async def _repository_for(
        self,
        session: AsyncSession,
    ) -> SQLAlchemyRuntimeConfigRepository | EnvironmentFallbackRuntimeConfigRepository:
        """仅对无法解密的 active 使用环境修复视图，数据库错误继续上抛。"""
        repository = SQLAlchemyRuntimeConfigRepository(session)
        if self._cipher is None:
            return repository
        state, active = await repository.get_activation_context()
        if active is None:
            return repository
        try:
            self._cipher.decrypt(bytes(active.encrypted_payload))
        except (InvalidToken, RuntimeConfigPayloadError, ValueError):
            previous_version_id: int | None = None
            if state.previous_version_id is not None:
                previous = await repository.get_version(int(state.previous_version_id))
                if previous is not None:
                    try:
                        self._cipher.decrypt(bytes(previous.encrypted_payload))
                    except (InvalidToken, RuntimeConfigPayloadError, ValueError):
                        pass
                    else:
                        previous_version_id = int(previous.id)
            return EnvironmentFallbackRuntimeConfigRepository(
                repository,
                previous_version_id=previous_version_id,
            )
        return repository

    async def _service(self, session: AsyncSession) -> RuntimeConfigService:
        """按当前 active 健康度创建绑定同一短会话的核心服务。"""
        if self._cipher is None:
            raise RuntimeConfigUnavailableError("CONFIG_ENCRYPTION_KEY 未配置")
        auth: Any = UnavailableAdminReverify()
        if (
            self._writable
            and self._dummy_hash is not None
            and self._argon2_semaphore is not None
            and self._argon2_executor is not None
        ):
            auth = AdminAuthService(
                SQLAlchemyAdminCredentialRepository(session),
                password_hasher=self._password_hasher,
                dummy_hash=self._dummy_hash,
                argon2_semaphore=self._argon2_semaphore,
                argon2_executor=self._argon2_executor,
            )
        async def activate_runtime(
            snapshot: RuntimeConfigSnapshot,
            version_id: int,
            revision: int,
        ) -> None:
            """先提交DB激活，再无事务构造并原子发布候选bundle。"""
            if self._runtime_activator is not None:
                await session.commit()
                await self._runtime_activator(snapshot, revision)
                return
            if self._registry is None or self._bundle_builder is None:
                return
            await session.commit()
            candidate = await self._bundle_builder(snapshot, revision)
            try:
                await self._registry.swap(candidate)
            except BaseException:
                await candidate.aclose()
                raise

        return RuntimeConfigService(
            repository=cast(Any, await self._repository_for(session)),
            cipher=self._cipher,
            auth=auth,
            tester=self._tester,
            environment_snapshot=self._environment_snapshot,
            # 候选落库后先提交，后续测试阶段不能长期持有事务或数据库锁。
            before_test=session.commit,
            activate_runtime=activate_runtime,
            # 补偿和失败审计必须在传播请求取消前真正落库。
            after_compensation=session.commit,
        )

    async def page_data(self) -> RuntimeConfigPage:
        """返回可读设置页；缺主密钥时回退环境掩码或空白修复投影。"""
        async with self._factory() as session:
            if self._cipher is not None:
                page = await (await self._service(session)).page_data()
            else:
                repository = SQLAlchemyRuntimeConfigRepository(session)
                state = await repository.get_state()
                page = RuntimeConfigPage(
                    view=(
                        self._environment_snapshot.masked_view()
                        if self._environment_snapshot is not None
                        else RuntimeConfigView.empty()
                    ),
                    revision=int(state.revision),
                    active_version_id=state.active_version_id,
                    previous_version_id=state.previous_version_id,
                    source=(
                        "environment"
                        if self._environment_snapshot is not None
                        else "unconfigured"
                    ),
                )
            await session.commit()
            return RuntimeConfigPage(
                view=page.view,
                revision=page.revision,
                active_version_id=page.active_version_id,
                previous_version_id=page.previous_version_id,
                source=page.source,
                writable=self._writable,
            )

    async def list_version_views(
        self,
        *,
        limit: int = 20,
    ) -> list[RuntimeConfigVersionView]:
        """返回不解密历史密文的安全版本列表。"""
        async with self._factory() as session:
            repository = SQLAlchemyRuntimeConfigRepository(session)
            state = await repository.get_state()
            versions = await repository.list_versions(limit=limit)
            result = [
                RuntimeConfigVersionView(
                    version_id=int(version.id),
                    created_at=version.created_at,
                    created_by_label=(
                        "YuMi 管理员" if version.created_by is not None else "系统"
                    ),
                    status=version.status.value,
                    failure_code=version.failure_code,
                    is_active=version.id == state.active_version_id,
                    is_previous=version.id == state.previous_version_id,
                    masked_summary=dict(version.masked_summary),
                    provider_results=safe_provider_results(version.test_results),
                )
                for version in versions
            ]
            await session.commit()
            return result

    async def create_and_test(
        self,
        command: UpdateRuntimeConfig,
        *,
        actor_id: int,
        admin_id: int,
        password: str,
        expected_session_version: int,
        expected_revision: int,
    ) -> ActivationResult:
        """用单会话保存候选，并在测试前释放事务、激活后原子提交审计。"""
        if not self._writable:
            raise RuntimeConfigUnavailableError("运行配置当前只读")
        async with self._activation_lock:
            if self._closing:
                raise RuntimeConfigUnavailableError("运行配置服务正在关闭")
            async with self._factory() as session:
                try:
                    result = await (await self._service(session)).create_and_test(
                        command,
                        actor_id=actor_id,
                        admin_id=admin_id,
                        password=password,
                        expected_session_version=expected_session_version,
                        expected_revision=expected_revision,
                    )
                except RuntimeConfigCompensationConflictError:
                    await session.commit()
                    if self._runtime_consistency_setter is not None:
                        self._runtime_consistency_setter(False)
                    raise
                except (
                    AuthenticationError,
                    RuntimeConfigConflictError,
                    RuntimeConfigTestError,
                ):
                    # 认证失败计数、失败候选和补偿审计都属于应持久化的安全结果。
                    await session.commit()
                    raise
                except Exception:
                    await session.rollback()
                    raise
                await session.commit()
                await self._mark_runtime_consistent_if_current(result.revision)
                return result

    async def rollback(
        self,
        *,
        actor_id: int,
        admin_id: int,
        password: str,
        expected_session_version: int,
        expected_revision: int,
        expected_previous_version_id: int,
    ) -> ActivationResult:
        """在单一事务中原子提交回滚指针和安全审计。"""
        if not self._writable:
            raise RuntimeConfigUnavailableError("运行配置当前只读")
        async with self._activation_lock:
            if self._closing:
                raise RuntimeConfigUnavailableError("运行配置服务正在关闭")
            async with self._factory() as session:
                try:
                    result = await (await self._service(session)).rollback(
                        actor_id=actor_id,
                        admin_id=admin_id,
                        password=password,
                        expected_session_version=expected_session_version,
                        expected_revision=expected_revision,
                        expected_previous_version_id=expected_previous_version_id,
                    )
                except RuntimeConfigCompensationConflictError:
                    await session.commit()
                    if self._runtime_consistency_setter is not None:
                        self._runtime_consistency_setter(False)
                    raise
                except (
                    AuthenticationError,
                    RuntimeConfigConflictError,
                    RuntimeConfigTestError,
                ):
                    await session.commit()
                    raise
                except Exception:
                    await session.rollback()
                    raise
                await session.commit()
                await self._mark_runtime_consistent_if_current(result.revision)
                return result


class SessionAdminCsrfService:
    """为每次 nonce 签发或消费创建独立短事务。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory
        self._issue_lock = asyncio.Lock()

    async def issue(self, purpose: str, *, admin_id: int | None) -> str:
        """持久化 nonce 摘要并提交后返回随机明文。"""
        # 同一应用实例串行执行清理、计数和插入，保证活动容量硬上限。
        async with self._issue_lock, self._factory() as session:
            token = await AdminCsrfService(
                SQLAlchemyAdminCsrfRepository(session)
            ).issue(purpose, admin_id=admin_id)
            await session.commit()
            return token

    async def consume(
        self,
        token: str,
        purpose: str,
        *,
        admin_id: int | None,
    ) -> bool:
        """用独立事务原子消费 nonce 并立即提交结果。"""
        async with self._factory() as session:
            consumed = await AdminCsrfService(SQLAlchemyAdminCsrfRepository(session)).consume(
                token, purpose, admin_id=admin_id
            )
            await session.commit()
            return consumed


class SessionEmployeeAccessVerifier:
    """每个后台请求都联合复核唯一凭证与管理员员工身份。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def get_active_admin(self, admin_id: int, employee_id: int) -> Any:
        """在短会话中读取不含密码哈希的最新管理员投影。"""
        async with self._factory() as session:
            return await SQLAlchemyEmployeeRepository(session).get_active_admin(
                admin_id,
                employee_id,
            )


class SessionApprovalPageService:
    """为每次审批读取或确认创建完整且隔离的业务服务。"""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        registry: RuntimeClientRegistry,
    ) -> None:
        """保存数据库会话工厂和运行客户端provider。"""
        self._factory = factory
        self._registry = registry

    @staticmethod
    def _service(session: AsyncSession, hostex: HostexClient) -> ApprovalPageService:
        """用同一会话组装权限、审批仓储和下单状态机。"""
        booking = BookingService(
            SQLAlchemyApprovalRepository(session),
            SQLAlchemyPermissionChecker(session),
            hostex,
        )
        return ApprovalPageService(
            session=session,
            hostex=hostex,
            booking=booking,
        )

    async def get_detail(self, approval_id: int) -> dict[str, Any]:
        """在短会话中读取审批页全部数据。"""
        async with self._registry.acquire() as bundle, self._factory() as session:
            return await self._service(session, bundle.hostex).get_detail(approval_id)

    async def list_pending(self, *, offset: int, limit: int) -> list[BookingApproval]:
        """在短会话中按分页边界读取待处理审批。"""
        async with self._registry.acquire() as bundle, self._factory() as session:
            return await self._service(session, bundle.hostex).list_pending(
                offset=offset,
                limit=limit,
            )

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """在独立会话中执行带行锁和幂等保护的确认。"""
        async with self._registry.acquire() as bundle, self._factory() as session:
            result = await self._service(session, bundle.hostex).confirm(
                approval_id,
                employee_id,
                command,
            )
            await session.commit()
            return result


class SessionAdminDashboardService:
    """为每次总览读取创建独立只读数据库会话。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """在短会话中聚合运营快照，不提交任何数据。"""
        async with self._factory() as session:
            return await AdminDashboardService(session).snapshot(now)


class SessionTaskPageService:
    """为每次任务页面请求创建独立数据库会话。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        storage: PrivateFileStorage,
        upload_size_limit: int,
    ) -> None:
        """保存数据库会话工厂和私有附件限制。"""
        self._factory = factory
        self._storage = storage
        self._upload_size_limit = upload_size_limit

    @staticmethod
    def _service(session: AsyncSession) -> TaskPageService:
        """在同一事务装配任务查询、分派和状态机。"""
        repository = SQLAlchemyOperationsRepository(session)
        return TaskPageService(
            repository,
            BusinessTaskService(repository),
        )

    async def list_for(
        self,
        employee: Employee,
        *,
        offset: int,
        limit: int,
    ) -> list[Any]:
        """按分页边界返回当前员工可见的未关闭任务。"""
        async with self._factory() as session:
            return await self._service(session).list_for(
                employee,
                offset=offset,
                limit=limit,
            )

    async def detail_for(
        self,
        task_id: int,
        employee: Employee,
    ) -> dict[str, object]:
        """返回当前员工可见的安全详情。"""
        async with self._factory() as session:
            return await self._service(session).detail_for(task_id, employee)

    async def transition(
        self,
        task_id: int,
        employee: Employee,
        target: str,
    ) -> Any:
        """在独立事务推进任务状态。"""
        async with self._factory() as session:
            result = await self._service(session).transition(
                task_id,
                employee,
                target,
            )
            await session.commit()
            return result

    async def assign(
        self,
        task_id: int,
        employee: Employee,
        *,
        assigned_employee_id: int,
        property_id: int,
        service_date: date,
    ) -> Any:
        """在独立事务补齐信息并分派任务。"""
        async with self._factory() as session:
            result = await self._service(session).assign(
                task_id,
                employee,
                assigned_employee_id=assigned_employee_id,
                property_id=property_id,
                service_date=service_date,
            )
            await session.commit()
            return result

    async def assignment_options(self) -> dict[str, list[object]]:
        """返回启用员工和房间选项。"""
        async with self._factory() as session:
            return await self._service(session).assignment_options()

    async def update_checklist(
        self,
        task_id: int,
        employee: Employee,
        checklist: dict[str, bool],
    ) -> Any:
        """在独立事务保存执行员工检查清单。"""
        async with self._factory() as session:
            result = await self._service(session).update_checklist(
                task_id,
                employee,
                checklist,
            )
            await session.commit()
            return result

    async def upload_photo(
        self,
        task_id: int,
        employee: Employee,
        stream: BinaryIO,
        content_type: str,
    ) -> Any:
        """先校验任务权限，再保存文件引用；事务失败时清理孤儿文件。"""
        stored: StoredPrivateFile | None = None
        try:
            async with self._factory() as session:
                service = self._service(session)
                await service.require_evidence_editor(task_id, employee)
                stored = await self._storage.save_image(
                    stream,
                    content_type,
                    self._upload_size_limit,
                )
                repository = SQLAlchemyOperationsRepository(session)
                attachment = await repository.add_task_attachment(
                    task_id=task_id,
                    file_id=stored.file_id,
                    uploaded_by=employee.id,
                )
                await session.commit()
                return attachment
        except Exception:
            if stored is not None:
                self._storage.delete(stored.file_id)
            raise

    async def mark_ready(self, task_id: int, employee: Employee) -> Any:
        """在同一事务锁定任务和房态并标记可入住。"""
        async with self._factory() as session:
            repository = SQLAlchemyOperationsRepository(session)
            credential_repository = SQLAlchemyCredentialDeliveryRepository(session)
            state = await RoomReadinessService(
                repository,
                repository,
                CredentialDeliveryService(
                    credential_repository,
                    SQLAlchemyJobRepository(session),
                ),
            ).mark_ready(task_id, employee)
            await session.commit()
            return state

    async def revoke_ready(self, task_id: int, employee: Employee) -> Any:
        """由管理员撤回任务关联房间的可入住状态。"""
        async with self._factory() as session:
            repository = SQLAlchemyOperationsRepository(session)
            task = await repository.get_task(task_id)
            if task is None:
                raise LookupError("任务不存在")
            if task.property_id is None:
                raise ValueError("任务尚未关联房间")
            state = await RoomReadinessService(
                repository,
                repository,
            ).revoke_ready(task.property_id, employee)
            await session.commit()
            return state

    async def file_for(
        self,
        file_id: str,
        employee: Employee,
    ) -> StoredPrivateFile:
        """数据库授权通过后才解析服务器私有文件。"""
        async with self._factory() as session:
            await self._service(session).require_attachment_visible(
                file_id,
                employee,
            )
        return self._storage.open_for_read(file_id)


class SessionPropertyAdminService:
    """为每次房源管理请求创建独立事务并协调私有二维码。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        cipher: SensitiveDataCipher,
        storage: PrivateFileStorage,
        upload_size_limit: int,
    ) -> None:
        """保存数据库、加密和私有文件依赖。"""
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._upload_size_limit = upload_size_limit

    def _service(self, session: AsyncSession) -> PropertyAdminService:
        """在当前事务装配房源管理服务。"""
        return PropertyAdminService(session, self._cipher)

    async def list_all(self, employee: Employee) -> list[Any]:
        """返回管理员可见房源列表。"""
        async with self._factory() as session:
            return await self._service(session).list_all(employee)

    async def detail_for(
        self,
        property_id: int,
        employee: Employee,
    ) -> dict[str, object]:
        """返回不含凭证明文的房源详情。"""
        async with self._factory() as session:
            return await self._service(session).detail_for(
                property_id,
                employee,
            )

    async def update_profile(
        self,
        property_id: int,
        employee: Employee,
        fields: PropertyFields,
    ) -> Any:
        """在独立事务更新房源运营资料。"""
        async with self._factory() as session:
            result = await self._service(session).update_profile(
                property_id,
                employee,
                fields,
            )
            await session.commit()
            return result

    async def replace_credentials(
        self,
        property_id: int,
        employee: Employee,
        password: str,
        guide: str,
        stream: BinaryIO,
        content_type: str,
    ) -> Any:
        """先验证管理员并保存二维码，事务失败时删除新文件。"""
        PropertyAdminService.require_admin(employee)
        stored: StoredPrivateFile | None = None
        try:
            stored = await self._storage.save_image(
                stream,
                content_type,
                self._upload_size_limit,
            )
            async with self._factory() as session:
                credential = await self._service(session).replace_credentials(
                    property_id,
                    employee,
                    password=password,
                    guide=guide,
                    qr_file_id=stored.file_id,
                )
                await session.commit()
                return credential
        except Exception:
            if stored is not None:
                self._storage.delete(stored.file_id)
            raise

    async def qr_for(
        self,
        property_id: int,
        employee: Employee,
    ) -> StoredPrivateFile:
        """数据库授权并定位当前版本后读取私有二维码。"""
        async with self._factory() as session:
            file_id = await self._service(session).active_qr_file_id(
                property_id,
                employee,
            )
        return self._storage.open_for_read(file_id)


class SessionCustomerAdminService:
    """为每次客户 CRM 请求创建独立数据库事务。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        cipher: SensitiveDataCipher,
        *,
        tag_sync_enabled: bool = False,
        registry: RuntimeClientRegistry | None = None,
    ) -> None:
        """保存数据库会话工厂、脱敏服务和标签同步开关。"""
        self._factory = factory
        self._cipher = cipher
        self._tag_sync_enabled = tag_sync_enabled
        self._registry = registry

    def _service(
        self,
        session: AsyncSession,
        *,
        tag_sync_enabled: bool | None = None,
    ) -> CustomerAdminService:
        """在同一事务装配客户仓储和持久化任务队列。"""
        return CustomerAdminService(
            SQLAlchemyCustomerRepository(session),
            self._cipher,
            SQLAlchemyJobRepository(session),
            tag_sync_enabled=(
                self._tag_sync_enabled
                if tag_sync_enabled is None
                else tag_sync_enabled
            ),
        )

    async def list_customers(
        self,
        query: str | None,
        administrator: Employee,
        *,
        offset: int,
        limit: int,
    ) -> list[Any]:
        """按分页边界返回脱敏客户卡片。"""
        async with self._factory() as session:
            return await self._service(session).list_customers(
                query,
                administrator,
                offset=offset,
                limit=limit,
            )

    async def get_detail(
        self,
        customer_id: int,
        administrator: Employee,
    ) -> dict[str, Any]:
        """返回脱敏客户详情。"""
        async with self._factory() as session:
            return await self._service(session).get_detail(
                customer_id,
                administrator,
            )

    async def get_merge_detail(
        self,
        suggestion_id: int,
        administrator: Employee,
    ) -> dict[str, Any]:
        """返回脱敏合并人工复核信息。"""
        async with self._factory() as session:
            return await self._service(session).get_merge_detail(
                suggestion_id,
                administrator,
            )

    async def set_tags(
        self,
        customer_id: int,
        tag_ids: list[int],
        administrator: Employee,
    ) -> None:
        """在同一事务先保存本地标签，再按需登记同步任务。"""
        if self._registry is None:
            async with self._factory() as session:
                await self._service(session).set_tags(
                    customer_id,
                    tag_ids,
                    administrator,
                )
                await session.commit()
            return
        async with self._registry.acquire() as bundle, self._factory() as session:
            await self._service(
                session,
                tag_sync_enabled=bundle.contact_client is not None,
            ).set_tags(customer_id, tag_ids, administrator)
            await session.commit()

    async def create_manual_merge(
        self,
        source_customer_id: int,
        target_customer_id: int,
        administrator: Employee,
    ) -> int:
        """创建手动建议并提交，实际迁移仍等待二次确认。"""
        async with self._factory() as session:
            suggestion_id = await self._service(session).create_manual_merge(
                source_customer_id,
                target_customer_id,
                administrator,
            )
            await session.commit()
            return suggestion_id

    async def update_note(
        self,
        customer_id: int,
        note: str,
        administrator: Employee,
    ) -> None:
        """提交客户员工备注。"""
        async with self._factory() as session:
            await self._service(session).update_note(
                customer_id,
                note,
                administrator,
            )
            await session.commit()

    async def update_summary(
        self,
        customer_id: int,
        administrator: Employee,
        *,
        short_summary: str,
        long_summary: str,
        unresolved_items: list[str],
    ) -> None:
        """提交管理员更正后的客户摘要。"""
        async with self._factory() as session:
            await self._service(session).update_summary(
                customer_id,
                administrator,
                short_summary=short_summary,
                long_summary=long_summary,
                unresolved_items=unresolved_items,
            )
            await session.commit()

    async def delete_summary(
        self,
        customer_id: int,
        administrator: Employee,
    ) -> None:
        """删除客户摘要并提交最小审计。"""
        async with self._factory() as session:
            await self._service(session).delete_summary(
                customer_id,
                administrator,
            )
            await session.commit()

    async def review_merge(
        self,
        suggestion_id: int,
        administrator: Employee,
        *,
        accepted: bool,
    ) -> None:
        """提交管理员对客户合并建议的明确决定。"""
        async with self._factory() as session:
            await self._service(session).review_merge(
                suggestion_id,
                administrator,
                accepted=accepted,
            )
            await session.commit()


class SessionKnowledgeAdminService:
    """为每次管理操作使用独立数据库会话。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def list_all(self, *, offset: int, limit: int) -> list[Any]:
        """按分页边界返回知识条目。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).list_all(
                offset=offset,
                limit=limit,
            )

    async def get_detail(self, entry_id: int) -> Any:
        """在独立只读会话中返回知识详情。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).get_detail(entry_id)

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

    async def list_candidates(self, *, offset: int, limit: int) -> list[Any]:
        """按分页边界返回管理员可审核的高频 FAQ 候选。"""
        async with self._factory() as session:
            return await KnowledgeAdminService(session).list_candidates(
                offset=offset,
                limit=limit,
            )

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


class SessionComplaintAdminService:
    """为每次客诉页面操作使用独立事务。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """保存数据库会话工厂。"""
        self._factory = factory

    async def get_detail(
        self,
        review_id: int,
        *,
        before_message_id: int | None = None,
    ) -> dict[str, Any]:
        """读取客诉详情。"""
        async with self._factory() as session:
            return await ComplaintAdminService(
                session,
                TransactionalOutboxWeCom(session, source_message_id=f"complaint:{review_id}"),
            ).get_detail(review_id, before_message_id=before_message_id)

    async def update_draft(self, review_id: int, version: int, draft: str) -> None:
        """保存客诉草稿并提交。"""
        async with self._factory() as session:
            await ComplaintAdminService(
                session,
                TransactionalOutboxWeCom(session, source_message_id=f"complaint:{review_id}"),
            ).update_draft(review_id, version, draft)
            await session.commit()

    async def send(self, review_id: int, version: int, draft: str, employee_id: int) -> None:
        """登记客人回复、状态和审计。"""
        async with self._factory() as session:
            review = await SQLAlchemyComplaintRepository(session).get(review_id)
            delivery_phase = (
                f"retry-{review.version}"
                if review is not None and review.status is ComplaintReviewStatus.DELIVERY_FAILED
                else None
            )
            await ComplaintAdminService(
                session,
                TransactionalOutboxWeCom(
                    session,
                    source_message_id=f"complaint:{review_id}",
                    delivery_phase=delivery_phase,
                ),
            ).send(review_id, version, draft, employee_id)
            await session.commit()

    async def return_for_analysis(self, review_id: int, version: int, employee_id: int) -> None:
        """退回分析并提交审计。"""
        async with self._factory() as session:
            await ComplaintAdminService(
                session,
                TransactionalOutboxWeCom(session, source_message_id=f"complaint:{review_id}"),
            ).return_for_analysis(review_id, version, employee_id)
            await session.commit()

    async def cancel(self, review_id: int, version: int, employee_id: int) -> None:
        """关闭客诉并提交审计。"""
        async with self._factory() as session:
            await ComplaintAdminService(
                session,
                TransactionalOutboxWeCom(session, source_message_id=f"complaint:{review_id}"),
            ).cancel(review_id, version, employee_id)
            await session.commit()


def _register_faq_draft_handler(
    handlers: dict[str, JobHandler],
    session: AsyncSession,
    factory: Callable[[AsyncSession], JobHandler] | None,
) -> None:
    """为当前 worker 事务按需注册 FAQ 草稿处理器。"""
    if factory is not None:
        handlers["faq_draft_generate"] = factory(session)


def _register_hostex_event_handler(
    handlers: dict[str, JobHandler],
    session: AsyncSession,
    factory: Callable[[AsyncSession], JobHandler] | None,
) -> None:
    """为当前 worker 事务按需注册百居易事件处理器。"""
    if factory is not None:
        handlers["hostex_event"] = factory(session)


def _register_lifecycle_handler(
    handlers: dict[str, JobHandler],
    session: AsyncSession,
    factory: Callable[[AsyncSession], JobHandler] | None,
) -> None:
    """为当前 worker 事务按需注册入住生命周期发送处理器。"""
    if factory is not None:
        handlers["lifecycle_send"] = factory(session)


def _register_credential_part_handler(
    handlers: dict[str, JobHandler],
    session: AsyncSession,
    factory: Callable[[AsyncSession], JobHandler] | None,
) -> None:
    """为当前 worker 事务按需注册凭证单部件发送处理器。"""
    if factory is not None:
        handlers["credential_send_part"] = factory(session)


def _register_customer_tag_handler(
    handlers: dict[str, JobHandler],
    session: AsyncSession,
    factory: Callable[[AsyncSession], JobHandler] | None,
) -> None:
    """在配置客户联系 Secret 后注册标签同步处理器。"""
    if factory is not None:
        handlers["customer_tag_sync"] = factory(session)


def _record_committed_job_heartbeat(
    app: FastAPI,
    job: Any,
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> None:
    """在任务最终提交后刷新对应业务链路的成功心跳。"""
    if job.job_type != "hostex_event":
        return
    completed_at = (now_provider or (lambda: datetime.now(UTC)))()
    app.state.hostex_sync_last_success = completed_at
    app.state.lifecycle_scheduler_last_success = completed_at


@dataclass(frozen=True, slots=True)
class RuntimeWorkerBindings:
    """固定单个job revision所需的企业微信处理器和业务handler。"""

    sync_handler: WeComSyncJobHandler
    wecom: WeComApiClient
    handlers: dict[str, JobHandler]


async def _run_worker_loop(
    app: FastAPI,
    *,
    factory: async_sessionmaker[AsyncSession],
    handler: WeComSyncJobHandler | None = None,
    wecom: WeComApiClient | None = None,
    registry: RuntimeClientRegistry | None = None,
    runtime_handler_factory: (
        Callable[[AsyncSession, RuntimeClientBundle], Any] | None
    ) = None,
    faq_draft_handler_factory: (Callable[[AsyncSession], JobHandler] | None) = None,
    complaint_review_handler_factory: (Callable[[AsyncSession], JobHandler] | None) = None,
    hostex_event_handler_factory: (Callable[[AsyncSession], JobHandler] | None) = None,
    credential_part_handler_factory: (Callable[[AsyncSession], JobHandler] | None) = None,
    customer_tag_handler_factory: (Callable[[AsyncSession], JobHandler] | None) = None,
    lifecycle_handler_factory: (Callable[[AsyncSession], JobHandler] | None) = None,
    deferred_message_handler: JobHandler | None = None,
    included_job_types: set[str] | None = None,
    excluded_job_types: set[str] | None = None,
    recover_stale: bool = True,
) -> None:
    """持续处理持久化任务；仅 recovery leader 负责恢复遗留锁。"""
    while True:
        runtime_lease: Any | None = None
        try:
            runtime_bundle: RuntimeClientBundle | None = None
            if registry is not None:
                runtime_lease = registry.acquire()
                runtime_bundle = await runtime_lease.__aenter__()
            async with factory() as session:
                if runtime_bundle is not None:
                    if runtime_handler_factory is None:
                        raise RuntimeError("运行时worker工厂尚未配置")
                    bindings = await runtime_handler_factory(session, runtime_bundle)
                    cycle_handler = bindings.sync_handler
                    cycle_wecom = bindings.wecom
                    runtime_handlers = bindings.handlers
                else:
                    if handler is None or wecom is None:
                        raise RuntimeError("固定worker客户端尚未配置")
                    cycle_handler = handler
                    cycle_wecom = wecom
                    runtime_handlers = {}
                repository = SQLAlchemyJobRepository(
                    session,
                    included_job_types=included_job_types,
                    excluded_job_types=excluded_job_types,
                )
                if recover_stale:
                    # 预订审批没有任务类型分片，只由通用 worker 负责恢复。
                    if included_job_types is None:
                        await SQLAlchemyApprovalRepository(session).recover_stale_creating(
                            before=datetime.now(UTC) - timedelta(minutes=5)
                        )
                    await repository.recover_stale(before=datetime.now(UTC) - timedelta(minutes=5))
                await session.commit()

                async def send_guest(
                    payload: dict[str, Any],
                    client: WeComApiClient = cycle_wecom,
                ) -> None:
                    """发送客人回复并回写真实 msgid，同时更新客诉投递状态。"""
                    source_message_id = str(payload.get("source_message_id", ""))
                    try:
                        real_message_id = await client.send_text(
                            str(payload["open_kfid"]),
                            str(payload["external_userid"]),
                            str(payload["content"]),
                        )
                    except Exception as error:
                        await _record_complaint_delivery(
                            session,
                            source_message_id,
                            delivered=False,
                            error_code=type(error).__name__,
                        )
                        if isinstance(error, httpx.ConnectError):
                            raise RetrySafeJobError("企业微信连接尚未建立") from error
                        if isinstance(error, WeComApiError) and error.error_code == 45009:
                            raise RetrySafeJobError("企业微信明确限流") from error
                        raise
                    await _record_complaint_delivery(
                        session,
                        source_message_id,
                        delivered=True,
                        external_message_id=real_message_id,
                    )
                    await SQLAlchemyMessageRepository(session).replace_external_message_id(
                        str(payload["outbox_id"]),
                        real_message_id,
                    )
                    conversation = await session.scalar(
                        select(Conversation).where(
                            Conversation.open_kfid == str(payload["open_kfid"]),
                            Conversation.external_userid == str(payload["external_userid"]),
                        )
                    )
                    if conversation is not None:
                        metadata: dict[str, Any] = {
                            "delivery_status": "accepted",
                            "delivery_retry_count": int(
                                payload.get("delivery_retry_count", 0) or 0
                            ),
                        }
                        retry_of_message_id = payload.get("retry_of_message_id")
                        if retry_of_message_id:
                            metadata["retry_of_message_id"] = str(retry_of_message_id)
                        await MessageService(SQLAlchemyMessageRepository(session)).record_bot(
                            conversation.id,
                            real_message_id,
                            str(payload["content"]),
                            message_type=str(payload.get("message_type", "text")),
                            metadata=metadata,
                        )

                async def send_internal(
                    payload: dict[str, Any],
                    client: WeComApiClient = cycle_wecom,
                ) -> None:
                    """发送员工通知；连接失败或明确限流时才允许有限重试。"""
                    try:
                        await client.send_internal_text(
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

                async def send_internal_card(
                    payload: dict[str, Any],
                    client: WeComApiClient = cycle_wecom,
                ) -> None:
                    """发送后台入口卡片；卡片本身不包含客人回复动作。"""
                    try:
                        await client.send_internal_card(
                            agent_id=int(payload["agent_id"]),
                            employee_userids=list(payload["employee_userids"]),
                            title=str(payload["title"]),
                            description=str(payload["description"]),
                            url=str(payload["url"]),
                        )
                    except httpx.ConnectError as error:
                        raise RetrySafeJobError("企业微信连接尚未建立") from error
                    except WeComApiError as error:
                        if error.error_code == 45009:
                            raise RetrySafeJobError("企业微信明确限流") from error
                        raise

                handlers: dict[str, JobHandler] = {
                    "wecom_sync": cycle_handler,
                    "wecom_send_text": send_guest,
                    "wecom_send_internal_text": send_internal,
                    "wecom_send_internal_card": send_internal_card,
                }
                handlers.update(runtime_handlers)
                if deferred_message_handler is not None:
                    handlers["wecom_process_message"] = deferred_message_handler
                _register_faq_draft_handler(
                    handlers,
                    session,
                    faq_draft_handler_factory,
                )
                if complaint_review_handler_factory is not None:
                    handlers["complaint_review_generate"] = complaint_review_handler_factory(
                        session
                    )
                _register_hostex_event_handler(
                    handlers,
                    session,
                    hostex_event_handler_factory,
                )
                _register_credential_part_handler(
                    handlers,
                    session,
                    credential_part_handler_factory,
                )
                _register_customer_tag_handler(
                    handlers,
                    session,
                    customer_tag_handler_factory,
                )
                _register_lifecycle_handler(
                    handlers,
                    session,
                    lifecycle_handler_factory,
                )
                worker = Worker(
                    repository=repository,
                    handlers=handlers,
                    heartbeat=lambda value: setattr(app.state, "worker_last_heartbeat", value),
                    checkpoint=session.commit,
                    on_job_committed=lambda job: _record_committed_job_heartbeat(app, job),
                )
                handled = await worker.run_once()
        except SQLAlchemyError as error:
            # 数据库提交或连接故障不得永久终止 worker；只记录异常类型并有限退避。
            if isinstance(error, OperationalError) and "database is locked" in str(error).lower():
                logger.warning("后台任务遇到 SQLite 写锁，1 秒后重试")
            else:
                logger.warning(
                    "后台任务遇到数据库运行故障，1 秒后重试：error_type=%s",
                    type(error).__name__,
                )
            await asyncio.sleep(1)
            continue
        finally:
            if runtime_lease is not None:
                await runtime_lease.__aexit__(None, None, None)
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
                await SQLAlchemyFaqCandidateRepository(session).maintain(now=current_time())
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


async def _run_retention_loop(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """每天在独立事务清理过期终态历史，失败时保活并等待下一轮。"""
    while True:
        try:
            async with factory() as session:
                deleted = await SQLAlchemyRetentionRepository(session).purge()
                await session.commit()
                logger.info(
                    "历史记录清理完成：jobs=%s external_requests=%s hostex_events=%s audit_logs=%s",
                    deleted.get("jobs", 0),
                    deleted.get("external_requests", 0),
                    deleted.get("hostex_webhook_events", 0),
                    deleted.get("audit_logs", 0),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "历史记录清理失败，下一轮继续：error_type=%s",
                type(error).__name__,
            )
        await asyncio.sleep(86_400)


async def _run_context_maintenance_loop(
    *,
    factory: async_sessionmaker[AsyncSession],
    summarizer: Any | None = None,
    registry: RuntimeClientRegistry | None = None,
    now_provider: Callable[[], datetime] | None = None,
    heartbeat_now: Callable[[], datetime] | None = None,
    heartbeat: Callable[[datetime], None] | None = None,
) -> None:
    """每小时为有消息的正式客户更新分层摘要。"""
    current_time = now_provider or (lambda: datetime.now(UTC))
    completed_time = heartbeat_now or (lambda: datetime.now(UTC))

    async def maintain_round(selected_summarizer: Any) -> None:
        """用同一个summarizer完成本轮全部客户，避免跨revision。"""
        async with factory() as discovery_session:
            repository = SQLAlchemyContextRepository(discovery_session)
            customer_ids = await repository.list_customer_ids_with_messages()
        cycle_now = current_time()
        for customer_id in customer_ids:
            try:
                # 每个客户独立会话和事务，模型超时或数据库异常不得污染其他客户。
                async with factory() as customer_session:
                    service = ContextRetentionService(
                        SQLAlchemyContextRepository(customer_session),
                        selected_summarizer,
                        before_external=customer_session.commit,
                    )
                    await service.maintain_customer(customer_id, cycle_now)
                    await customer_session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "单客户上下文维护失败：customer_id=%s error_type=%s",
                    customer_id,
                    type(error).__name__,
                )

    while True:
        try:
            if registry is not None:
                async with registry.acquire() as bundle:
                    await maintain_round(bundle.context_summarizer)
            else:
                if summarizer is None:
                    raise RuntimeError("客户上下文摘要器尚未配置")
                await maintain_round(summarizer)
            if heartbeat is not None:
                heartbeat(completed_time())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # 摘要失败时事务回滚并保留原文，下一周期自动重试。
            logger.warning(
                "客户上下文维护失败：error_type=%s",
                type(error).__name__,
            )
        await asyncio.sleep(3600)


async def _run_hostex_reconcile_loop(
    *,
    factory: async_sessionmaker[AsyncSession],
    hostex: HostexClient | None = None,
    interval_seconds: float | None = None,
    registry: RuntimeClientRegistry | None = None,
    runtime_lifecycle_factory: (
        Callable[[AsyncSession, RuntimeClientBundle], LifecycleReminderService] | None
    ) = None,
    today_provider: Callable[[], date] | None = None,
    lifecycle_factory: (Callable[[AsyncSession], LifecycleReminderService] | None) = None,
    heartbeat_now: Callable[[], datetime] | None = None,
    sync_heartbeat: Callable[[datetime], None] | None = None,
    lifecycle_heartbeat: Callable[[datetime], None] | None = None,
) -> None:
    """定时对账近期订单，补回遗漏的 Webhook。"""
    current_date = today_provider or (lambda: datetime.now(UTC).date())
    current_time = heartbeat_now or (lambda: datetime.now(UTC))
    while True:
        try:
            if registry is not None:
                async with registry.acquire() as bundle, factory() as session:
                    lifecycle = (
                        runtime_lifecycle_factory(session, bundle)
                        if runtime_lifecycle_factory is not None
                        else None
                    )
                    service = HostexSyncService(
                        bundle.hostex,
                        SQLAlchemyOperationsRepository(session),
                        lifecycle=lifecycle,
                    )
                    today = current_date()
                    await service.reconcile(
                        today - timedelta(days=1),
                        today + timedelta(days=15),
                    )
                    await session.commit()
            else:
                if hostex is None:
                    raise RuntimeError("百居易对账客户端尚未配置")
                async with factory() as session:
                    service = HostexSyncService(
                        hostex,
                        SQLAlchemyOperationsRepository(session),
                        lifecycle=(
                            lifecycle_factory(session) if lifecycle_factory is not None else None
                        ),
                    )
                    today = current_date()
                    await service.reconcile(
                        today - timedelta(days=1),
                        today + timedelta(days=15),
                    )
                    await session.commit()
            completed_at = current_time()
            if sync_heartbeat is not None:
                sync_heartbeat(completed_at)
            if lifecycle_heartbeat is not None:
                lifecycle_heartbeat(completed_at)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "百居易订单对账失败：error_type=%s",
                type(error).__name__,
            )
        if registry is not None:
            async with registry.acquire() as interval_bundle:
                next_interval = interval_bundle.hostex_reconcile_interval_seconds
        elif interval_seconds is not None:
            next_interval = interval_seconds
        else:
            raise RuntimeError("百居易对账间隔尚未配置")
        await asyncio.sleep(next_interval)


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
    poller: WeComMessagePoller | None = None,
    interval_seconds: float | None = None,
    registry: RuntimeClientRegistry | None = None,
    runtime_poller_factory: Callable[[RuntimeClientBundle], WeComMessagePoller] | None = None,
) -> None:
    """周期补拉客服消息；失败时退避，成功时更新健康心跳。"""
    delay = interval_seconds
    backing_off = False
    while True:
        if registry is not None:
            async with registry.acquire() as interval_bundle:
                current_interval = interval_bundle.wecom_poll_interval_seconds
            if not backing_off:
                delay = current_interval
        elif interval_seconds is not None:
            current_interval = interval_seconds
        else:
            raise RuntimeError("企业微信补拉间隔尚未配置")
        if delay is None:
            delay = current_interval
        await asyncio.sleep(delay)
        try:
            if registry is not None:
                if runtime_poller_factory is None:
                    raise RuntimeError("运行时企业微信补拉工厂尚未配置")
                async with registry.acquire() as bundle:
                    current_interval = bundle.wecom_poll_interval_seconds
                    await runtime_poller_factory(bundle).run_once()
            else:
                if poller is None:
                    raise RuntimeError("企业微信补拉器尚未配置")
                await poller.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            delay = _next_wecom_poll_delay(
                current_delay=delay,
                interval_seconds=current_interval,
                error=error,
            )
            backing_off = True
            # 只记录异常类型，避免企业微信错误正文携带请求细节。
            logger.warning(
                "企业微信定时补拉失败，%s 秒后重试：%s",
                delay,
                type(error).__name__,
            )
        else:
            app.state.wecom_poll_last_success = datetime.now(UTC)
            backing_off = False


LOCAL_ADMIN_WECOM_USERID = "local-admin-console"


async def _has_valid_existing_admin(session: AsyncSession) -> bool:
    """联合验证既有凭证、Argon2 基线和保留本地员工身份。"""
    row = (
        await session.execute(
            select(AdminCredential, Employee)
            .join(Employee, Employee.id == AdminCredential.employee_id)
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return False
    credential, employee = row
    try:
        validate_admin_password_hash(credential.password_hash)
    except ValueError:
        return False
    return (
        employee.wecom_userid == LOCAL_ADMIN_WECOM_USERID
        and employee.is_active
        and employee.role is EmployeeRole.ADMIN
    )


async def _upsert_local_admin_employee(session: AsyncSession) -> int:
    """按保留 userid 原子创建或修复本地管理员员工，并返回主键。"""
    values = {
        "wecom_userid": LOCAL_ADMIN_WECOM_USERID,
        "name": "本地后台管理员",
        "role": EmployeeRole.ADMIN,
        "is_active": True,
    }
    dialect_name = session.get_bind().dialect.name
    statement: Any
    if dialect_name == "sqlite":
        statement = sqlite_insert(Employee).values(**values)
    elif dialect_name == "postgresql":
        statement = postgresql_insert(Employee).values(**values)
    else:
        raise RuntimeError(f"不支持的管理员引导数据库方言: {dialect_name}")
    employee_id = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[Employee.wecom_userid],
            set_={
                "name": values["name"],
                "role": values["role"],
                "is_active": values["is_active"],
            },
        ).returning(Employee.id)
    )
    if employee_id is None:
        raise RuntimeError("本地后台管理员员工引导后无法读取")
    return int(employee_id)


async def _bootstrap_admin_auth(
    factory: async_sessionmaker[AsyncSession],
    *,
    username: str | None,
    password_hash: str | None,
) -> bool:
    """优先复用既有凭证；首次引导只绑定明确的本地审计员工。"""
    async with factory() as session:
        existing = await session.scalar(select(AdminCredential).limit(1))
        if existing is not None:
            return await _has_valid_existing_admin(session)
        if username is None or password_hash is None:
            return False
        try:
            employee_id = await _upsert_local_admin_employee(session)
            await SQLAlchemyAdminCredentialRepository(session).bootstrap(
                employee_id=employee_id,
                username=username,
                password_hash=password_hash,
            )
            await session.commit()
        except IntegrityError:
            # 多实例唯一键竞争后回滚本事务，再以获胜实例提交的状态为准。
            await session.rollback()
        return await _has_valid_existing_admin(session)


async def _resolve_runtime_snapshot(
    factory: async_sessionmaker[AsyncSession],
    *,
    cipher: RuntimeConfigCipher | None,
    environment_snapshot: RuntimeConfigSnapshot | None,
) -> tuple[RuntimeConfigSnapshot | None, str, bool]:
    """按数据库优先规则解析启动快照，并把安全回退标为降级。"""
    async with factory() as session:
        _, active = await SQLAlchemyRuntimeConfigRepository(session).get_activation_context()
        await session.commit()
    if active is None:
        source = "environment" if environment_snapshot is not None else "unconfigured"
        return environment_snapshot, source, cipher is None
    if cipher is not None:
        try:
            return cipher.decrypt(bytes(active.encrypted_payload)), "database", False
        except (InvalidToken, RuntimeConfigPayloadError, ValueError) as error:
            logger.warning("激活运行配置不可解密，已进入安全修复模式：%s", type(error).__name__)
    else:
        logger.warning("配置主密钥缺失，激活运行配置不可读取，已进入安全修复模式")
    source = "environment_fallback" if environment_snapshot is not None else "repair_only"
    return environment_snapshot, source, True


def _clear_lifespan_state(app: FastAPI) -> None:
    """清除生命周期注入对象，避免同进程测试重启复用已关闭资源。"""
    for state_name in (
        "admin_auth_service",
        "admin_auth_available",
        "admin_login_rate_limiter",
        "admin_argon2_semaphore",
        "admin_csrf_service",
        "employee_access_verifier",
        "approval_page_service",
        "admin_dashboard_service",
        "task_page_service",
        "private_file_service",
        "property_admin_service",
        "customer_admin_service",
        "knowledge_admin_service",
        "complaint_admin_service",
        "runtime_config_service",
        "runtime_config_source",
        "runtime_config_writes_available",
        "runtime_configuration_consistent",
        "runtime_resources_healthy",
        "runtime_client_registry",
        "wecom_callback_service",
        "hostex_webhook_service",
        "health_service",
        "started_at",
        "worker_last_heartbeat",
        "wecom_poll_last_success",
        "hostex_sync_last_success",
        "context_maintenance_last_success",
        "lifecycle_scheduler_last_success",
    ):
        if hasattr(app.state, state_name):
            delattr(app.state, state_name)


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """先装配安全后台，再按数据库优先来源启用外部客户端和 worker。"""
    try:
        bootstrap = BootstrapSettings()  # type: ignore[call-arg]
    except ValidationError:
        # 基础配置不完整时数据库和登录都不可安全构造，只保留公开降级健康页。
        yield
        return

    try:
        runtime_environment = RuntimeEnvironmentSettings()  # type: ignore[call-arg]
    except ValidationError:
        environment_snapshot = None
    else:
        # 环境只在启动时捕获一次，后续页面操作不得重新读取变化中的进程环境。
        environment_snapshot = RuntimeConfigSnapshot.from_settings(runtime_environment)

    runtime_cipher: RuntimeConfigCipher | None = None
    if bootstrap.config_encryption_key is not None:
        try:
            runtime_cipher = RuntimeConfigCipher(bootstrap.config_encryption_key)
        except ValueError as error:
            logger.warning("配置主密钥不可用，设置页已切换只读：%s", type(error).__name__)

    engine = create_engine(bootstrap.database_url)
    factory = create_session_factory(engine)
    try:
        admin_auth_available = await _bootstrap_admin_auth(
            factory,
            username=bootstrap.admin_bootstrap_username,
            password_hash=bootstrap.admin_bootstrap_password_hash,
        )
    except Exception as error:
        # 后台引导失败不能阻断企业微信、客服和 worker 主链路，且日志不含秘密正文。
        admin_auth_available = False
        logger.warning("管理员后台引导不可用：%s", type(error).__name__)

    runtime_snapshot, runtime_source, runtime_degraded = await _resolve_runtime_snapshot(
        factory,
        cipher=runtime_cipher,
        environment_snapshot=environment_snapshot,
    )
    app.state.runtime_config_source = runtime_source
    app.state.admin_auth_available = admin_auth_available
    app.state.admin_login_rate_limiter = AdminLoginRateLimiter()
    argon2_executor: ThreadPoolExecutor | None = None
    argon2_semaphore: asyncio.Semaphore | None = None
    dummy_hash: str | None = None
    if admin_auth_available:
        # 虚拟哈希在应用生命周期仅生成一次，登录和设置复核共享有界线程池。
        argon2_semaphore = asyncio.Semaphore(2)
        argon2_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="admin-argon2",
        )
        async with argon2_semaphore:
            dummy_hash = await asyncio.get_running_loop().run_in_executor(
                argon2_executor,
                ADMIN_PASSWORD_HASHER.hash,
                "admin-auth-dummy-password",
            )
        app.state.admin_argon2_semaphore = argon2_semaphore
        app.state.admin_auth_service = SessionAdminAuthService(
            factory,
            password_hasher=ADMIN_PASSWORD_HASHER,
            dummy_hash=dummy_hash,
            argon2_semaphore=argon2_semaphore,
            argon2_executor=argon2_executor,
        )
        app.state.admin_csrf_service = SessionAdminCsrfService(factory)
        app.state.employee_access_verifier = SessionEmployeeAccessVerifier(factory)

    runtime_writable = runtime_cipher is not None and admin_auth_available
    app.state.runtime_config_writes_available = runtime_writable
    runtime_config_service = SessionRuntimeConfigService(
        factory,
        cipher=runtime_cipher,
        environment_snapshot=environment_snapshot,
        password_hasher=ADMIN_PASSWORD_HASHER,
        dummy_hash=dummy_hash,
        argon2_semaphore=argon2_semaphore,
        argon2_executor=argon2_executor,
        writable=runtime_writable,
        # 只有生产生命周期显式装配真实外联测试器；直接构造服务默认零网络。
        tester=RuntimeConfigTester(),
    )
    app.state.runtime_config_service = runtime_config_service

    sensitive_data = SensitiveDataCipher(bootstrap.data_encryption_key)
    private_file_storage = PrivateFileStorage(bootstrap.private_upload_dir)
    app.state.admin_dashboard_service = SessionAdminDashboardService(factory)
    app.state.task_page_service = SessionTaskPageService(
        factory,
        private_file_storage,
        bootstrap.private_upload_max_bytes,
    )
    app.state.private_file_service = app.state.task_page_service
    app.state.property_admin_service = SessionPropertyAdminService(
        factory,
        sensitive_data,
        private_file_storage,
        bootstrap.private_upload_max_bytes,
    )
    app.state.customer_admin_service = SessionCustomerAdminService(
        factory,
        sensitive_data,
        tag_sync_enabled=(
            runtime_snapshot is not None and runtime_snapshot.wecom_contact_secret is not None
        ),
    )
    app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory)
    app.state.complaint_admin_service = SessionComplaintAdminService(factory)

    async def database_probe() -> bool:
        """执行无副作用 SELECT 1 检查数据库连接。"""
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def runtime_revision_provider() -> int:
        """以短只读会话返回当前数据库激活指针revision。"""
        async with factory() as session:
            state = await SQLAlchemyRuntimeConfigRepository(session).get_state()
            await session.commit()
            return int(state.revision)

    startup_time = datetime.now(UTC)
    app.state.started_at = startup_time
    app.state.worker_last_heartbeat = startup_time
    app.state.wecom_poll_last_success = startup_time
    app.state.hostex_sync_last_success = startup_time
    app.state.context_maintenance_last_success = startup_time
    app.state.lifecycle_scheduler_last_success = startup_time
    app.state.runtime_configuration_consistent = True
    app.state.runtime_resources_healthy = True
    web_search_state = WebSearchState()
    app.state.health_service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: app.state.worker_last_heartbeat,
        poll_heartbeat_getter=lambda: app.state.wecom_poll_last_success,
        hostex_heartbeat_getter=lambda: app.state.hostex_sync_last_success,
        context_heartbeat_getter=lambda: app.state.context_maintenance_last_success,
        lifecycle_heartbeat_getter=lambda: app.state.lifecycle_scheduler_last_success,
        configuration_ok=False,
        web_search_status_getter=web_search_state.get,
        contact_sync_configured=False,
    )

    queue = DurableJobQueue(factory)
    knowledge = KnowledgeService(SessionKnowledgeRepository(factory))
    faq_candidate_context = FaqCandidateContextService(SessionFaqCandidateRepository(factory))
    event_recorder = SessionHostexEventRecorder(factory)
    runtime_registry: RuntimeClientRegistry | None = None
    background_tasks: list[asyncio.Task[None]] = []
    runtime_start_lock = asyncio.Lock()
    runtime_closing = False

    async def build_candidate_bundle(
        snapshot: RuntimeConfigSnapshot,
        revision: int,
    ) -> RuntimeClientBundle:
        """复用启动期稳定依赖构造一个可原子发布的候选bundle。"""
        return await build_runtime_client_bundle(
            snapshot,
            revision=revision,
            callback_queue=queue,
            hostex_event_recorder=event_recorder,
            knowledge=knowledge,
            faq_candidate_context=faq_candidate_context,
            safety_hmac_key=bootstrap.session_secret.encode(),
            web_search_status_setter=web_search_state.set,
        )

    async def handle_message(
        message: IncomingMessage,
        bundle: RuntimeClientBundle,
        *,
        deferred: bool = False,
    ) -> None:
        """在独立事务中处理入站消息或执行已提交的最终回复任务。"""
        async with factory() as session:
            faq_candidates = SQLAlchemyFaqCandidateRepository(session)
            context_repository = SQLAlchemyContextRepository(session)
            service = ConversationService(
                conversations=SQLAlchemyConversationRepository(session),
                messages=MessageService(SQLAlchemyMessageRepository(session)),
                assistant=bundle.assistant,
                emergency_service=EmergencyService(),
                wecom=TransactionalOutboxWeCom(
                    session,
                    source_message_id=message.msgid,
                    delivery_phase="final" if deferred else None,
                ),
                agent_id=bundle.agent_id,
                duty_employee_userids=list(bundle.duty_userids),
                approvals=ApprovalService(SQLAlchemyApprovalRepository(session)),
                approval_base_url=bootstrap.public_base_url,
                frequent_faq=FrequentFaqService(
                    candidates=faq_candidates,
                    jobs=SQLAlchemyJobRepository(session),
                    savepoint_factory=session.begin_nested,
                ),
                customer_profiles=CustomerService(
                    SQLAlchemyCustomerRepository(session),
                    sensitive_data,
                ),
                customer_context=context_repository,
                room_assignment=context_repository,
                business_tasks=BusinessTaskService(SQLAlchemyOperationsRepository(session)),
                audit_events=SQLAlchemyOperationsRepository(session),
                jobs=SQLAlchemyJobRepository(session),
                identity_resolver=bundle.wecom,
                complaint_service=ComplaintService(),
                complaint_reviews=SQLAlchemyComplaintRepository(session),
                defer_model=not deferred,
                commit_boundary=session.commit if not deferred else None,
            )
            if deferred:
                await service.process_recorded_message(message)
            else:
                await service.handle_message(message)
            await session.commit()

    async def handle_deferred_message(
        payload: dict[str, Any],
        bundle: RuntimeClientBundle,
    ) -> None:
        """执行已提交入站消息的最终模型回复。"""
        message = IncomingMessage(
            msgid=str(payload["msgid"]),
            open_kfid=str(payload["open_kfid"]),
            external_userid=str(payload["external_userid"]),
            origin=MessageOrigin(str(payload["origin"])),
            msgtype=str(payload["msgtype"]),
            content=str(payload.get("content", "")),
            sent_at=datetime.fromisoformat(str(payload["sent_at"])),
        )
        await handle_message(message, bundle, deferred=True)

    def build_lifecycle_service(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> LifecycleReminderService:
        """用同一事务装配提醒状态、任务队列、发送器和人工任务。"""
        return LifecycleReminderService(
            SQLAlchemyLifecycleReminderRepository(session),
            SQLAlchemyJobRepository(session),
            bundle.wecom,
            BusinessTaskService(SQLAlchemyOperationsRepository(session)),
            weather=bundle.reminder_weather,
            before_external=session.commit,
        )

    async def handle_send_failure(
        external_message_id: str,
        fail_type: int,
        bundle: RuntimeClientBundle,
    ) -> None:
        """在独立事务消费企业微信异步发送失败事件。"""
        async with factory() as session:
            complaint = await SQLAlchemyComplaintRepository(
                session
            ).mark_delivery_failed_by_external_message_id(
                external_message_id,
                error_code=f"wecom_async_{fail_type}",
            )
            if complaint is None:
                # 普通机器人消息失败时自动重试一次；客诉消息仍走人工重发流程。
                retry_queued = await _handle_guest_delivery_failure(
                    session,
                    external_message_id,
                    fail_type=fail_type,
                )
                if not retry_queued:
                    await _notify_guest_delivery_failure(
                        session,
                        external_message_id,
                        agent_id=bundle.agent_id,
                        employee_userids=list(bundle.duty_userids),
                    )
            await build_lifecycle_service(session, bundle).handle_send_failure(
                external_message_id,
                fail_type,
            )
            await session.commit()

    def build_sync_handler(bundle: RuntimeClientBundle) -> WeComSyncJobHandler:
        """为单次poll或job固定同一revision的消息与失败处理器。"""
        return WeComSyncJobHandler(
            api=bundle.wecom,
            handle_message=lambda message: handle_message(message, bundle),
            handle_send_failure=lambda message_id, fail_type: handle_send_failure(
                message_id,
                fail_type,
                bundle,
            ),
            enqueue=queue.enqueue,
        )

    def build_faq_draft_handler(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> JobHandler:
        """为当前 worker 会话创建可原子保存草稿和通知的处理器。"""

        async def handle_faq_draft(payload: dict[str, Any]) -> None:
            """按候选代次生成草稿，并把管理员通知写入同一事务。"""
            candidate_id = int(payload["candidate_id"])
            generation = int(payload["generation"])
            service = FaqDraftJobService(
                candidates=SQLAlchemyFaqCandidateRepository(session),
                drafter=bundle.faq_drafter,
                knowledge=KnowledgeService(
                    cast(
                        Any,
                        SQLAlchemyKnowledgeRepository(session),
                    )
                ),
                administrators=SQLAlchemyEmployeeRepository(session),
                notifications=TransactionalOutboxWeCom(
                    session,
                    source_message_id=(f"faq-draft:{candidate_id}:{generation}"),
                ),
                agent_id=bundle.agent_id,
                knowledge_admin_url=(
                    f"{bootstrap.public_base_url.rstrip('/')}/employee/knowledge"
                ),
            )
            await service.handle(payload)

        return handle_faq_draft

    def build_complaint_review_handler(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> JobHandler:
        """为客诉后台任务装配分析、脱敏上下文和员工通知。"""

        async def handle_complaint_review(payload: dict[str, Any]) -> None:
            """生成客诉分析并把后台编辑入口写入事务型发件箱。"""
            service = ComplaintReviewJobService(
                reviews=SQLAlchemyComplaintRepository(session),
                analyzer=bundle.complaint_analyzer,
                messages=SQLAlchemyComplaintMessageContext(SQLAlchemyMessageRepository(session)),
                notifications=TransactionalOutboxWeCom(
                    session,
                    source_message_id=f"complaint-review:{payload['review_id']}",
                ),
                employee_userids=list(bundle.duty_userids),
                agent_id=bundle.agent_id,
                edit_url=(
                    f"{bootstrap.public_base_url.rstrip('/')}/employee/complaints"
                ),
            )
            await service.handle(payload)

        return handle_complaint_review

    def build_hostex_event_handler(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> JobHandler:
        """为当前 worker 事务创建百居易事件同步处理器。"""
        service = HostexSyncService(
            bundle.hostex,
            SQLAlchemyOperationsRepository(session),
            lifecycle=build_lifecycle_service(session, bundle),
            before_external=session.commit,
        )

        async def handle_hostex_event(payload: dict[str, Any]) -> None:
            """按事件键同步一笔订单。"""
            await service.handle_event(str(payload["event_key"]))

        return handle_hostex_event

    def build_lifecycle_handler(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> JobHandler:
        """为当前 worker 事务创建主动提醒发送处理器。"""
        service = build_lifecycle_service(session, bundle)

        async def handle_lifecycle(payload: dict[str, Any]) -> None:
            """按提醒编号执行发送前复核与状态回写。"""
            await service.deliver(int(payload["reminder_id"]))

        return handle_lifecycle

    def build_credential_part_handler(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> JobHandler:
        """为当前 worker 事务创建禁止盲目重放的凭证部件发送器。"""
        sender = CredentialPartSender(
            SQLAlchemyCredentialDeliveryRepository(session),
            bundle.wecom,
            sensitive_data,
            private_file_storage,
            before_external=session.commit,
        )
        return sender.handle

    def build_customer_tag_handler(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> JobHandler:
        """为当前 worker 事务创建幂等客户标签同步处理器。"""
        if bundle.contact_client is None:
            raise RuntimeError("企业微信客户联系尚未配置")
        service = CustomerTagSyncService(
            SQLAlchemyCustomerRepository(session),
            bundle.contact_client,
        )
        return service.handle

    async def build_worker_bindings(
        session: AsyncSession,
        bundle: RuntimeClientBundle,
    ) -> RuntimeWorkerBindings:
        """为一个已领取job一次性组装全部同revision处理器。"""
        handlers: dict[str, JobHandler] = {
            "faq_draft_generate": build_faq_draft_handler(session, bundle),
            "complaint_review_generate": build_complaint_review_handler(session, bundle),
            "hostex_event": build_hostex_event_handler(session, bundle),
            "credential_send_part": build_credential_part_handler(session, bundle),
            "lifecycle_send": build_lifecycle_handler(session, bundle),
            "wecom_process_message": lambda payload: handle_deferred_message(
                payload,
                bundle,
            ),
        }
        if bundle.contact_client is not None:
            handlers["customer_tag_sync"] = build_customer_tag_handler(session, bundle)
        return RuntimeWorkerBindings(
            sync_handler=build_sync_handler(bundle),
            wecom=bundle.wecom,
            handlers=handlers,
        )

    async def start_runtime_services(
        snapshot: RuntimeConfigSnapshot,
        revision: int,
        *,
        configuration_healthy: bool = True,
    ) -> None:
        """串行执行首次完整装配或后续swap，失败时不发布半成品运行时。"""
        nonlocal runtime_registry
        async with runtime_start_lock:
            if runtime_closing:
                raise RuntimeError("应用运行时正在关闭")
            candidate = await build_candidate_bundle(snapshot, revision)
            if runtime_closing:
                await complete_cleanup(candidate.aclose())
                raise RuntimeError("应用运行时正在关闭")
            if runtime_registry is not None:
                try:
                    await runtime_registry.swap(candidate)
                except BaseException:
                    with contextlib.suppress(BaseException):
                        await candidate.aclose()
                    raise
                return

            candidate_registry = RuntimeClientRegistry(
                candidate,
                resource_health_setter=lambda value: setattr(
                    app.state,
                    "runtime_resources_healthy",
                    value,
                ),
                configuration_healthy=configuration_healthy,
            )
            approval_service = SessionApprovalPageService(
                factory=factory,
                registry=candidate_registry,
            )
            customer_service = SessionCustomerAdminService(
                factory,
                sensitive_data,
                registry=candidate_registry,
            )
            # 启动宽限期避免首次补拉前被误报；真实心跳会覆盖初始时间。
            health_service = OperationalHealthService(
                database_probe=database_probe,
                heartbeat_getter=lambda: app.state.worker_last_heartbeat,
                poll_heartbeat_getter=lambda: app.state.wecom_poll_last_success,
                hostex_heartbeat_getter=(lambda: app.state.hostex_sync_last_success),
                context_heartbeat_getter=(
                    lambda: app.state.context_maintenance_last_success
                ),
                lifecycle_heartbeat_getter=(
                    lambda: app.state.lifecycle_scheduler_last_success
                ),
                configuration_ok=admin_auth_available and runtime_writable,
                web_search_status_getter=web_search_state.get,
                runtime_status_provider=candidate_registry.status,
                runtime_revision_provider=runtime_revision_provider,
            )
            started_tasks: list[asyncio.Task[None]] = []
            try:
                started_tasks.append(
                    _create_runtime_task(
                        _run_worker_loop(
                            app,
                            factory=factory,
                            registry=candidate_registry,
                            runtime_handler_factory=build_worker_bindings,
                            excluded_job_types={"wecom_process_message"},
                            recover_stale=True,
                        )
                    )
                )
                started_tasks.append(
                    _create_runtime_task(
                        _run_worker_loop(
                            app,
                            factory=factory,
                            registry=candidate_registry,
                            runtime_handler_factory=build_worker_bindings,
                            included_job_types={"wecom_process_message"},
                            recover_stale=True,
                        )
                    )
                )
                started_tasks.append(
                    _create_runtime_task(
                        _run_wecom_poll_loop(
                            app,
                            registry=candidate_registry,
                            runtime_poller_factory=lambda bundle: WeComMessagePoller(
                                api=bundle.wecom,
                                handler=build_sync_handler(bundle),
                            ),
                        )
                    )
                )
                started_tasks.append(
                    _create_runtime_task(_run_faq_maintenance_loop(factory=factory))
                )
                started_tasks.append(_create_runtime_task(_run_retention_loop(factory)))
                started_tasks.append(
                    _create_runtime_task(
                        _run_context_maintenance_loop(
                            factory=factory,
                            registry=candidate_registry,
                            heartbeat=lambda value: setattr(
                                app.state,
                                "context_maintenance_last_success",
                                value,
                            ),
                        )
                    )
                )
                started_tasks.append(
                    _create_runtime_task(
                        _run_hostex_reconcile_loop(
                            factory=factory,
                            registry=candidate_registry,
                            runtime_lifecycle_factory=build_lifecycle_service,
                            sync_heartbeat=lambda value: setattr(
                                app.state,
                                "hostex_sync_last_success",
                                value,
                            ),
                            lifecycle_heartbeat=lambda value: setattr(
                                app.state,
                                "lifecycle_scheduler_last_success",
                                value,
                            ),
                        )
                    )
                )
            except BaseException:
                for task in started_tasks:
                    task.cancel()
                for task in started_tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                with contextlib.suppress(BaseException):
                    await candidate_registry.close()
                raise

            # create_task在下一次让出事件循环前不会运行，故此处原子发布完整state。
            app.state.approval_page_service = approval_service
            app.state.customer_admin_service = customer_service
            app.state.health_service = health_service
            app.state.runtime_client_registry = candidate_registry
            runtime_registry = candidate_registry
            background_tasks.extend(started_tasks)

    runtime_config_service.configure_runtime_activation(
        runtime_activator=start_runtime_services,
    )

    async def shutdown_runtime() -> None:
        """等待配置操作与运行装配退出后，再清理全部生命周期资源。"""
        # 配置操作可在tester、补偿或提交阶段，须先等待整个临界区退出。
        await runtime_config_service.wait_for_activation_idle()
        # closing在外层同步置位；锁仅作为在途构造/发布完成的屏障。
        async with runtime_start_lock:
            pass
        try:
            for task in background_tasks:
                task.cancel()
            for task in background_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        finally:
            try:
                if runtime_registry is not None:
                    await runtime_registry.close()
            finally:
                _clear_lifespan_state(app)
                if argon2_executor is not None:
                    # 等待仍在执行的 Argon2 线程退出，并取消尚未开始的排队任务。
                    await asyncio.to_thread(
                        argon2_executor.shutdown,
                        wait=True,
                        cancel_futures=True,
                    )
                await engine.dispose()

    try:
        if runtime_snapshot is not None:
            async with factory() as revision_session:
                runtime_state = await SQLAlchemyRuntimeConfigRepository(
                    revision_session
                ).get_state()
                initial_revision = int(runtime_state.revision)
                await revision_session.commit()
            await start_runtime_services(
                runtime_snapshot,
                initial_revision,
                configuration_healthy=not runtime_degraded,
            )
        yield
    except asyncio.CancelledError as error:
        runtime_closing = True
        runtime_config_service.begin_closing()
        await complete_cleanup(shutdown_runtime(), pending_cancel=error)
        raise error
    except BaseException:
        runtime_closing = True
        runtime_config_service.begin_closing()
        await complete_cleanup(shutdown_runtime())
        raise
    else:
        runtime_closing = True
        runtime_config_service.begin_closing()
        await complete_cleanup(shutdown_runtime())
