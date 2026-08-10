import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from admin_auth_helpers import configure_admin_auth
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import (
    EmployeeRole,
    KnowledgeCandidateStatus,
    Language,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    KnowledgeCandidate,
)
from homestay_bot.repositories.employees import SQLAlchemyEmployeeRepository
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.routes.knowledge import (
    KnowledgeAdminService,
)
from homestay_bot.routes.knowledge import (
    router as knowledge_router,
)
from homestay_bot.services.knowledge_service import KnowledgeService


@dataclass
class EntryStub:
    """模拟可启停的双语知识条目。"""

    id: int
    category: str
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str
    keywords: list[str]
    is_enabled: bool = True


@dataclass
class CandidateStub:
    """模拟管理员页面中的高频 FAQ 候选。"""

    id: int
    canonical_question: str
    category: str
    total_occurrences: int
    examples: list[str]
    draft_payload: dict[str, object]


class KnowledgeAdminStub:
    """在内存中实现管理页和机器人共享的知识源。"""

    def __init__(self) -> None:
        self.list_all_calls: list[tuple[int, int]] = []
        self.list_candidate_calls: list[tuple[int, int]] = []
        self.entries = [
            EntryStub(
                id=1,
                category="入住",
                question_zh="几点入住？",
                answer_zh="下午三点后。",
                question_en="Check-in time?",
                answer_en="After 3 PM.",
                keywords=["入住"],
            )
        ]
        self.candidates = [
            CandidateStub(
                id=8,
                canonical_question="是否提供停车位？",
                category="交通",
                total_occurrences=3,
                examples=["能停车吗", "有停车位吗"],
                draft_payload={
                    "category": "交通",
                    "question_zh": "是否提供停车位？",
                    "answer_zh": "【待管理员确认】",
                    "question_en": "Is parking available?",
                    "answer_en": "【待管理员确认】",
                    "keywords": ["停车"],
                    "verification_items": ["停车位置和收费规则"],
                },
            )
        ]
        self.converted: tuple[int, int, dict[str, object]] | None = None
        self.snoozed: tuple[int, int] | None = None

    async def list_all(self, *, offset: int, limit: int) -> list[EntryStub]:
        """返回全部条目供管理页展示。"""
        self.list_all_calls.append((offset, limit))
        return self.entries * (limit if offset == 50 else 1)

    async def list_active(self) -> list[EntryStub]:
        """只返回启用条目供机器人使用。"""
        return [entry for entry in self.entries if entry.is_enabled]

    async def get_detail(self, entry_id: int) -> EntryStub:
        """按编号返回知识详情，不存在时保持 404 语义。"""
        try:
            return next(entry for entry in self.entries if entry.id == entry_id)
        except StopIteration as error:
            raise LookupError("知识条目不存在") from error

    async def create(self, employee_id: int, **fields) -> EntryStub:
        """新增双语条目。"""
        entry = EntryStub(id=len(self.entries) + 1, **fields)
        self.entries.append(entry)
        return entry

    async def update(self, entry_id: int, employee_id: int, **fields) -> EntryStub:
        """更新指定条目。"""
        entry = next(item for item in self.entries if item.id == entry_id)
        for key, value in fields.items():
            setattr(entry, key, value)
        return entry

    async def set_enabled(
        self, entry_id: int, employee_id: int, enabled: bool
    ) -> None:
        """启用或停用指定条目。"""
        entry = next(item for item in self.entries if item.id == entry_id)
        entry.is_enabled = enabled

    async def list_candidates(
        self, *, offset: int, limit: int
    ) -> list[CandidateStub]:
        """返回管理员待归纳候选。"""
        self.list_candidate_calls.append((offset, limit))
        return self.candidates * (limit if offset == 50 else 1)

    async def convert_candidate(
        self,
        candidate_id: int,
        employee_id: int,
        **fields,
    ) -> EntryStub:
        """记录管理员采用并修改后的候选草稿。"""
        self.converted = (candidate_id, employee_id, fields)
        return await self.create(employee_id, **fields)

    async def snooze_candidate(self, candidate_id: int, employee_id: int) -> None:
        """记录管理员关闭候选。"""
        self.snoozed = (candidate_id, employee_id)


def build_client(
    role: EmployeeRole,
) -> tuple[TestClient, KnowledgeAdminStub]:
    """创建带测试登录入口的知识管理应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.include_router(knowledge_router)
    configure_admin_auth(app, role)
    service = KnowledgeAdminStub()
    app.state.knowledge_admin_service = service

    @app.post("/test/login")
    async def test_login(request: Request) -> dict[str, bool]:
        """仅在测试应用中写入可信员工会话。"""
        request.session["employee_id"] = (
            1 if role is EmployeeRole.ADMIN else 2
        )
        request.session["employee_role"] = role.value
        request.session["admin_id"] = 1
        request.session["admin_session_version"] = 1
        request.session["last_activity_at"] = datetime.now(UTC).isoformat()
        return {"ok": True}

    client = TestClient(app)
    client.post("/test/login")
    return client, service


def test_regular_customer_service_can_read_but_cannot_modify() -> None:
    """普通客服可查看知识，但看不到候选且所有修改接口均应拒绝。"""
    client, _ = build_client(EmployeeRole.STAFF)

    detail = client.get("/employee/knowledge")
    disable = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": "not-used-without-admin-role"},
    )

    assert detail.status_code == 200
    assert "几点入住" in detail.text
    assert "是否提供停车位" not in detail.text
    assert disable.status_code == 403


def test_knowledge_pages_use_admin_shell_and_detail_respects_role() -> None:
    """知识详情复用现有编辑入口，员工只读、管理员可编辑。"""
    admin, _ = build_client(EmployeeRole.ADMIN)
    staff, _ = build_client(EmployeeRole.STAFF)

    index = admin.get("/employee/knowledge")
    detail = admin.get("/employee/knowledge/1")
    staff_detail = staff.get("/employee/knowledge/1")
    missing = admin.get("/employee/knowledge/404")

    assert '/static/admin.js' in index.text
    assert 'href="/employee/knowledge" aria-current="page"' in detail.text
    assert 'action="/employee/knowledge/1/edit"' in detail.text
    assert 'data-unsaved-warning' in detail.text
    assert 'action="/employee/knowledge/1/disable"' not in detail.text
    assert 'action="/employee/knowledge/1/disable"' in index.text
    assert 'action="/employee/knowledge/1/edit"' not in staff_detail.text
    assert "下午三点后" in staff_detail.text
    assert missing.status_code == 404


def test_knowledge_index_orders_filters_candidates_and_entries() -> None:
    """列表页从上到下展示说明筛选、候选、新增与现有条目。"""
    client, _ = build_client(EmployeeRole.ADMIN)

    response = client.get("/employee/knowledge")

    assert response.text.index("知识筛选") < response.text.index("待归纳问题")
    assert response.text.index("待归纳问题") < response.text.index("新增知识")
    assert response.text.index("新增知识") < response.text.index(
        'id="knowledge-entries"'
    )
    assert 'data-unsaved-warning' in response.text
    assert 'action="/employee/knowledge/1/disable" data-confirm=' in response.text


def test_knowledge_lists_use_independent_bounded_pagination() -> None:
    """正式知识和 FAQ 候选必须分别分页且保留彼此页码。"""
    client, service = build_client(EmployeeRole.ADMIN)

    response = client.get("/employee/knowledge?page=2&candidate_page=2")

    assert response.status_code == 200
    assert service.list_all_calls == [(50, 51)]
    assert service.list_candidate_calls == [(50, 51)]
    assert (
        'href="/employee/knowledge?page=1&amp;candidate_page=2"'
        in response.text
    )
    assert (
        'href="/employee/knowledge?page=2&amp;candidate_page=3"'
        in response.text
    )


@pytest.mark.asyncio
async def test_disabling_knowledge_removes_it_from_bot_context() -> None:
    """停用内容必须立即退出机器人可用上下文。"""
    client, repository = build_client(EmployeeRole.ADMIN)
    knowledge_service = KnowledgeService(repository)

    page = client.get("/employee/knowledge")
    csrf_token = re.search(
        r'name="csrf_token" value="([^"]+)"', page.text
    ).group(1)
    response = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": csrf_token},
    )
    context = await knowledge_service.build_context(Language.ZH)

    assert response.status_code == 204
    assert 1 not in {item.source_id for item in context}


def test_admin_can_create_bilingual_knowledge() -> None:
    """管理员新增时必须同时提交中英文问答。"""
    client, service = build_client(EmployeeRole.ADMIN)

    page = client.get("/employee/knowledge")
    csrf_token = re.search(
        r'name="csrf_token" value="([^"]+)"', page.text
    ).group(1)
    response = client.post(
        "/employee/knowledge",
        data={
            "category": "交通",
            "question_zh": "怎么到民宿？",
            "answer_zh": "请按导航前往。",
            "question_en": "How can I get there?",
            "answer_en": "Please follow the map.",
            "keywords": "交通,导航",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert service.entries[-1].question_en == "How can I get there?"


def test_admin_can_view_and_edit_draft_before_conversion() -> None:
    """管理员页面应展示脱敏示例，并按修改后的双语内容转换候选。"""
    client, service = build_client(EmployeeRole.ADMIN)

    page = client.get("/employee/knowledge")
    csrf_token = re.search(
        r'name="csrf_token" value="([^"]+)"', page.text
    ).group(1)
    assert "待归纳问题" in page.text
    assert "能停车吗" in page.text
    assert "停车位置和收费规则" in page.text

    response = client.post(
        "/employee/knowledge/candidates/8/convert",
        data={
            "category": "交通",
            "question_zh": "民宿是否提供停车位？",
            "answer_zh": "院外有公共停车位，收费以现场为准。",
            "question_en": "Is parking available?",
            "answer_en": "Public parking is available nearby.",
            "keywords": "停车,自驾",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert service.converted is not None
    assert service.converted[0:2] == (8, 1)
    assert service.converted[2]["answer_zh"] == "院外有公共停车位，收费以现场为准。"


def test_candidate_actions_require_admin_and_one_time_csrf() -> None:
    """候选转换和关闭必须同时通过管理员权限与一次性 CSRF。"""
    regular_client, regular_service = build_client(EmployeeRole.STAFF)
    forbidden = regular_client.post(
        "/employee/knowledge/candidates/8/snooze",
        data={"csrf_token": "ignored"},
    )
    assert forbidden.status_code == 403
    assert regular_service.snoozed is None

    admin_client, admin_service = build_client(EmployeeRole.ADMIN)
    invalid = admin_client.post(
        "/employee/knowledge/candidates/8/snooze",
        data={"csrf_token": "invalid"},
    )
    assert invalid.status_code == 409
    assert admin_service.snoozed is None

    page = admin_client.get("/employee/knowledge")
    csrf_token = re.search(
        r'name="csrf_token" value="([^"]+)"', page.text
    ).group(1)
    accepted = admin_client.post(
        "/employee/knowledge/candidates/8/snooze",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    replayed = admin_client.post(
        "/employee/knowledge/candidates/8/snooze",
        data={"csrf_token": csrf_token},
    )

    assert accepted.status_code == 303
    assert replayed.status_code == 409
    assert admin_service.snoozed == (8, 1)


@pytest.mark.asyncio
async def test_admin_service_audit_does_not_copy_knowledge_body() -> None:
    """知识变更审计只记录条目与动作，不复制问答正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        employee = Employee(
            wecom_userid="admin-1",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.commit()
        service = KnowledgeAdminService(session)
        entry = await service.create(
            employee.id,
            category="入住",
            question_zh="敏感问题正文",
            answer_zh="敏感答案正文",
            question_en="Sensitive question",
            answer_en="Sensitive answer",
            keywords=["入住"],
        )
        audit = await session.scalar(select(AuditLog))

        assert audit is not None
        assert audit.target_id == str(entry.id)
        assert "敏感" not in str(audit.details)

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_service_converts_candidate_and_clears_private_content() -> None:
    """候选转换、启用正式知识、隐私清理和最小审计必须处于同一事务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        employee = Employee(
            wecom_userid="admin-1",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.flush()
        candidates = SQLAlchemyFaqCandidateRepository(session)
        candidate = await candidates.get_or_create(
            canonical_question="是否提供停车位？",
            category="交通",
        )
        await candidates.add_occurrence(
            candidate.id,
            source_message_id="guest-msg-1",
            occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
            example="能停车吗",
        )
        await candidates.mark_draft_ready(
            candidate.id,
            {
                "question_zh": "敏感草稿问题",
                "answer_zh": "敏感草稿答案",
            },
        )
        service = KnowledgeAdminService(session)

        entry = await service.convert_candidate(
            candidate.id,
            employee.id,
            category="交通",
            question_zh="民宿是否提供停车位？",
            answer_zh="请按管理员确认后的停车说明执行。",
            question_en="Is parking available?",
            answer_en="Please follow the confirmed parking instructions.",
            keywords=["停车"],
        )
        converted = await session.get(KnowledgeCandidate, candidate.id)
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "faq_candidate.convert")
        )

        assert entry.is_enabled is True
        assert converted is not None
        assert converted.status is KnowledgeCandidateStatus.CONVERTED
        assert converted.knowledge_entry_id == entry.id
        assert converted.examples == []
        assert converted.draft_payload is None
        assert audit is not None
        assert audit.details == {
            "candidate_id": candidate.id,
            "knowledge_entry_id": entry.id,
        }
        assert "敏感" not in str(audit.details)

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_service_snoozes_candidate_for_thirty_days() -> None:
    """暂不收录应关闭三十天、清除正文并写入不含正文的审计。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)

    async with factory() as session:
        employee = Employee(
            wecom_userid="admin-1",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.flush()
        candidates = SQLAlchemyFaqCandidateRepository(session)
        candidate = await candidates.get_or_create(
            canonical_question="是否可以寄存行李？",
            category="服务",
        )
        await candidates.add_occurrence(
            candidate.id,
            source_message_id="guest-msg-2",
            occurred_at=now,
            example="敏感示例正文",
        )
        service = KnowledgeAdminService(session, now=lambda: now)

        await service.snooze_candidate(candidate.id, employee.id)
        snoozed = await session.get(KnowledgeCandidate, candidate.id)
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "faq_candidate.snooze")
        )

        assert snoozed is not None
        assert snoozed.status is KnowledgeCandidateStatus.SNOOZED
        assert snoozed.snoozed_until == now + timedelta(days=30)
        assert snoozed.examples == []
        assert audit is not None
        assert audit.details == {"candidate_id": candidate.id}
        assert "敏感" not in str(audit.details)

    await engine.dispose()


@pytest.mark.asyncio
async def test_employee_repository_lists_only_active_admin_userids() -> None:
    """管理员提醒收件人不得包含普通员工或停用管理员。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                Employee(
                    wecom_userid="admin-active",
                    name="启用管理员",
                    role=EmployeeRole.ADMIN,
                ),
                Employee(
                    wecom_userid="admin-disabled",
                    name="停用管理员",
                    role=EmployeeRole.ADMIN,
                    is_active=False,
                ),
                Employee(
                    wecom_userid="staff-active",
                    name="普通客服",
                    role=EmployeeRole.STAFF,
                ),
            ]
        )
        await session.commit()

        userids = await SQLAlchemyEmployeeRepository(
            session
        ).list_active_admin_userids()

        assert userids == ["admin-active"]

    await engine.dispose()
