"""验证 AI 调试前后生产业务表保持不变且外部写接口不可达。"""

import json
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
from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.integrations.deepseek_client import AssistantDecision
from homestay_bot.services import runtime_clients
from homestay_bot.services.admin_debug_service import (
    AdminDebugRateLimiter,
    AdminDebugService,
    DebugPreviewCommand,
)
from homestay_bot.services.runtime_clients import RuntimeClientRegistry


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


class ProductionToolCompletions:
    """模拟 SDK 首轮发起合法房态工具，次轮返回结构化决定。"""

    def __init__(self) -> None:
        """初始化请求记录。"""
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """依次返回 tool call 和 AssistantDecision JSON。"""
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            function = SimpleNamespace(
                name="search_availability",
                arguments=(
                    '{"check_in_date":"2026-08-12",'
                    '"check_out_date":"2026-08-13"}'
                ),
            )
            call = SimpleNamespace(id="debug-call-1", function=function)
            message = SimpleNamespace(
                content=None,
                tool_calls=[call],
                model_dump=lambda **kwargs: {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "debug-call-1",
                            "type": "function",
                            "function": {
                                "name": function.name,
                                "arguments": function.arguments,
                            },
                        }
                    ],
                },
            )
        else:
            payload = {
                "reply_text": "生产助手只读查询完成。",
                "language": "zh",
                "intent": "availability_query",
                "confidence": 0.95,
                "handoff_reason": None,
                "booking_fields": {
                    "check_in_date": "2026-08-12",
                    "check_out_date": "2026-08-13",
                },
                "knowledge_gap": False,
                "knowledge_gap_topic": None,
                "staff_confirmation_required": False,
                "staff_confirmation_reason": None,
                "faq_candidate": False,
                "faq_candidate_id": None,
                "faq_canonical_question": None,
                "faq_category": None,
                "task_suggestion": None,
            }
            message = SimpleNamespace(
                content=json.dumps(payload, ensure_ascii=False),
                tool_calls=None,
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    """提供生产 assistant 所需的无网络 OpenAI SDK 表面。"""

    def __init__(self, **kwargs) -> None:
        """保存 completions fake，忽略凭证构造参数。"""
        self.chat = SimpleNamespace(completions=ProductionToolCompletions())

    async def close(self) -> None:
        """允许生产 bundle 正常释放资源。"""


class FakeAnthropic:
    """提供旅游搜索构造所需的无网络 Anthropic SDK 表面。"""

    def __init__(self, **kwargs) -> None:
        """接受生产构造参数但不发起请求。"""

    async def close(self) -> None:
        """允许生产 bundle 正常释放资源。"""


class FakeRawHttp:
    """模拟由 SDK 接管前的 HTTP 客户端。"""

    async def aclose(self) -> None:
        """支持构造失败清理。"""


class HostexReadWriteSpy:
    """实现合法只读接口，并让任何写接口立即失败。"""

    def __init__(self, token: str) -> None:
        """初始化读取次数和零写入次数。"""
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    async def list_properties(self):
        """返回生产工具可序列化的房源投影。"""
        self.read_calls.append("list_properties")
        return [
            SimpleNamespace(
                id=11,
                title="江汉路一号房",
                model_dump=lambda mode: {"id": 11, "title": "江汉路一号房"},
            )
        ]

    async def list_availabilities(self, property_ids, start_date, end_date):
        """返回生产工具可序列化的房态投影。"""
        self.read_calls.append("list_availabilities")
        return [
            SimpleNamespace(
                property_id=11,
                model_dump=lambda mode: {"property_id": 11, "available": True},
            )
        ]

    async def list_reference_prices(self, start_date, end_date):
        """保留第三项只读接口，本测试不应使用。"""
        self.read_calls.append("list_reference_prices")
        return []

    async def create_reservation(self, *args, **kwargs):
        """禁止生产调试创建订单。"""
        self.write_calls.append("create_reservation")
        raise AssertionError("create_reservation 不得调用")

    async def mark_tag(self, *args, **kwargs):
        """禁止生产调试修改标签。"""
        self.write_calls.append("mark_tag")
        raise AssertionError("mark_tag 不得调用")

    async def aclose(self) -> None:
        """允许生产 bundle 正常释放百居易资源。"""


class WeComFailFastSpy:
    """让发送和转人工路径可观察且调用即失败。"""

    def __init__(self, *args) -> None:
        """初始化零写入记录。"""
        self.write_calls: list[str] = []

    async def send_text(self, *args, **kwargs):
        """禁止向客人发送消息。"""
        self.write_calls.append("send_text")
        raise AssertionError("send_text 不得调用")

    async def transfer(self, *args, **kwargs):
        """禁止转人工。"""
        self.write_calls.append("transfer")
        raise AssertionError("transfer 不得调用")

    async def aclose(self) -> None:
        """允许生产 bundle 正常释放企微资源。"""


class ContactFailFastSpy:
    """让客户标签写路径可观察且调用即失败。"""

    def __init__(self, *args) -> None:
        """初始化零写入记录。"""
        self.write_calls: list[str] = []

    async def mark_tag(self, *args, **kwargs):
        """禁止修改企业微信客户标签。"""
        self.write_calls.append("mark_tag")
        raise AssertionError("mark_tag 不得调用")

    async def aclose(self) -> None:
        """允许生产 bundle 正常释放客户联系资源。"""


class EmptyKnowledge:
    """为生产 assistant 提供空审核知识。"""

    async def build_context(self, language: Language):
        """返回空知识列表。"""
        return []


class EmptyFaqContext:
    """为生产 assistant 提供空 FAQ 候选目录。"""

    async def build_context(self):
        """返回空候选列表。"""
        return []


def production_snapshot() -> RuntimeConfigSnapshot:
    """构造可通过生产 bundle 校验的完整运行配置。"""
    return RuntimeConfigSnapshot(
        deepseek_api_key="deepseek-test",
        deepseek_base_url="https://deepseek.example",
        deepseek_model="deepseek-model",
        hostex_access_token="hostex-test",
        hostex_webhook_secret_token="webhook-test",
        hostex_reconcile_interval_seconds=600.0,
        wecom_corp_id="corp-test",
        wecom_kf_secret="kf-test",
        wecom_callback_token="callback-test",
        wecom_encoding_aes_key="A" * 43,
        wecom_agent_id=100001,
        wecom_agent_secret="agent-test",
        wecom_contact_secret="contact-test",
        wecom_duty_userids="owner",
        wecom_poll_interval_seconds=10.0,
    )


@pytest.mark.asyncio
async def test_production_bundle_assistant_is_read_only_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产 bundle 到真实 assistant、service 和 SQLite 全链只能执行只读工具。"""
    hostex = HostexReadWriteSpy("hostex-test")
    wecom = WeComFailFastSpy()
    contact = ContactFailFastSpy()
    monkeypatch.setattr(runtime_clients, "AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr(runtime_clients, "AsyncAnthropic", FakeAnthropic)
    monkeypatch.setattr(
        runtime_clients,
        "build_public_https_client",
        lambda policy: FakeRawHttp(),
    )
    monkeypatch.setattr(runtime_clients, "HostexClient", lambda token: hostex)
    monkeypatch.setattr(runtime_clients, "WeComApiClient", lambda *args: wecom)
    monkeypatch.setattr(runtime_clients, "WeComContactClient", lambda *args: contact)

    bundle = await runtime_clients.build_runtime_client_bundle(
        production_snapshot(),
        revision=9,
        callback_queue=SimpleNamespace(),
        hostex_event_recorder=SimpleNamespace(),
        knowledge=EmptyKnowledge(),
        faq_candidate_context=EmptyFaqContext(),
        safety_hmac_key=b"debug-test-key",
        web_search_status_setter=lambda value: None,
    )
    registry = RuntimeClientRegistry(bundle)
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
        registry=registry,
        properties=SessionDebugPropertyRepository(factory),
        audits=SessionDebugAuditRepository(factory),
        limiter=AdminDebugRateLimiter(limit=10),
        local_date_provider=lambda: date(2026, 8, 11),
    )
    try:
        result = await service.preview(
            DebugPreviewCommand(
                actor_employee_id=1,
                admin_id=1,
                question="2026-08-12 入住、2026-08-13 退房有房吗？",
                language=Language.ZH,
                property_id=11,
                check_in_date=date(2026, 8, 12),
                check_out_date=date(2026, 8, 13),
            )
        )
        async with factory() as session:
            after = await counts(session)
            audits = list((await session.scalars(select(AuditLog))).all())
    finally:
        await registry.close()
        await engine.dispose()

    assert result.reply_text == "生产助手只读查询完成。"
    assert result.revision == 9
    assert [trace.name for trace in result.tool_trace] == ["search_availability"]
    assert hostex.read_calls == ["list_properties", "list_availabilities"]
    assert hostex.write_calls == []
    assert wecom.write_calls == []
    assert contact.write_calls == []
    assert after == before
    assert len(audits) == 1
    assert audits[0].details["tool_names"] == ["search_availability"]
