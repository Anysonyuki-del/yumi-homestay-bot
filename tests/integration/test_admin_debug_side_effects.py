"""验证 AI 调试前后生产业务表保持不变且外部写接口不可达。"""

from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import (
    SessionDebugAuditRepository,
    SessionDebugPropertyRepository,
)
from homestay_bot.domain.enums import EmployeeRole, Language
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BookingApproval,
    BusinessTask,
    ComplaintReview,
    Conversation,
    Employee,
    Job,
    Message,
    PropertyProfile,
)
from homestay_bot.integrations.deepseek_client import AssistantDecision
from homestay_bot.services.admin_debug_service import (
    AdminDebugRateLimiter,
    AdminDebugService,
    DebugPreviewCommand,
)


class FailFastWrites:
    """任何消息、转人工、下单或标签写操作都会立即让测试失败。"""

    async def send_text(self, *args, **kwargs):
        """禁止发送客人消息。"""
        raise AssertionError("send_text 不得调用")

    async def transfer(self, *args, **kwargs):
        """禁止转人工。"""
        raise AssertionError("transfer 不得调用")

    async def create_reservation(self, *args, **kwargs):
        """禁止创建订单。"""
        raise AssertionError("create_reservation 不得调用")

    async def mark_tag(self, *args, **kwargs):
        """禁止修改客户标签。"""
        raise AssertionError("mark_tag 不得调用")


class ReadOnlyAssistant:
    """模拟模型预览，仅写当前请求 trace sink。"""

    async def respond(self, **kwargs):
        """返回固定决定，不访问任何生产服务。"""
        return AssistantDecision(
            reply_text="仅预览",
            language=Language.ZH,
            intent="faq",
            confidence=0.9,
        )


class Registry:
    """提供包含 fail-fast 写客户端的固定 revision bundle。"""

    def __init__(self) -> None:
        """保存不可触达的写接口。"""
        forbidden = FailFastWrites()
        self.bundle = SimpleNamespace(
            revision=3,
            assistant=ReadOnlyAssistant(),
            wecom=forbidden,
            hostex=forbidden,
            contact_client=forbidden,
        )

    @asynccontextmanager
    async def acquire(self):
        """提供一次固定 bundle 租约。"""
        yield self.bundle


async def counts(session) -> dict[str, int]:
    """统计六张生产业务表，明确排除允许新增的安全 AuditLog。"""
    models = (Conversation, Message, Job, BusinessTask, ComplaintReview, BookingApproval)
    return {
        model.__tablename__: int(await session.scalar(select(func.count(model.id))) or 0)
        for model in models
    }


@pytest.mark.asyncio
async def test_preview_changes_only_safe_audit_log() -> None:
    """真实临时数据库中调试前后六张生产表计数必须完全一致。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                Employee(
                    id=1,
                    wecom_userid="admin",
                    name="管理员",
                    role=EmployeeRole.ADMIN,
                    is_active=True,
                ),
                PropertyProfile(id=11, title="江汉路一号房", is_active=True),
            ]
        )
        await session.commit()
        before = await counts(session)

    service = AdminDebugService(
        registry=Registry(),
        properties=SessionDebugPropertyRepository(factory),
        audits=SessionDebugAuditRepository(factory),
        limiter=AdminDebugRateLimiter(limit=10),
        local_date_provider=lambda: date(2026, 8, 11),
    )
    result = await service.preview(
        DebugPreviewCommand(
            actor_employee_id=1,
            admin_id=1,
            question="几点入住？",
            language=Language.ZH,
            property_id=11,
            check_in_date=date(2026, 8, 12),
            check_out_date=date(2026, 8, 13),
        )
    )

    async with factory() as session:
        after = await counts(session)
        audit_count = int(await session.scalar(select(func.count(AuditLog.id))) or 0)
    assert result.reply_text == "仅预览"
    assert after == before
    assert audit_count == 1
    await engine.dispose()
