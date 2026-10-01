import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

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
    scope: str = "global"
    property_id: int | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    trigger_any: list[str] | None = None
    trigger_exclude: list[str] | None = None


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
                    "scope": "global",
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

    async def list_all(
        self,
        *,
        offset: int,
        limit: int,
        query: str | None = None,
        enabled: bool | None = None,
        category: str | None = None,
        room: str | None = None,
    ) -> list[EntryStub]:
        """按管理页筛选返回条目并记录分页边界。"""
        self.list_all_calls.append((offset, limit))
        entries = [
            entry
            for entry in self.entries
            if (enabled is None or entry.is_enabled is enabled)
            and (not category or entry.category == category)
            and (not room or str(getattr(entry, "property_id", None)) == room)
            and (not query or query in entry.question_zh or query in entry.answer_zh)
        ]
        return entries * (limit if offset == 50 else 1)

    async def list_active(self) -> list[EntryStub]:
        """只返回启用条目供机器人使用。"""
        return [entry for entry in self.entries if entry.is_enabled]

    async def get_detail(self, entry_id: int) -> EntryStub:
        """按编号返回知识详情，不存在时保持 404 语义。"""
        try:
            return next(entry for entry in self.entries if entry.id == entry_id)
        except StopIteration as error:
            raise LookupError("知识条目不存在") from error

    async def list_images(self, entry_id: int) -> list[object]:
        """详情页配图区：存根条目没有配图。"""
        return []

    async def image_counts(self, entry_ids: list[int]) -> dict[int, int]:
        """存根条目没有配图。"""
        return {}

    async def list_properties(self):
        """提供合成房间选项。"""
        return []

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

    async def set_enabled(self, entry_id: int, employee_id: int, enabled: bool) -> None:
        """启用或停用指定条目。"""
        entry = next(item for item in self.entries if item.id == entry_id)
        entry.is_enabled = enabled

    async def list_candidates(self, *, offset: int, limit: int) -> list[CandidateStub]:
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

    async def delete_entry(self, entry_id: int, employee_id: int) -> None:
        """删除条目；不存在时保持 LookupError 语义。"""
        entry = await self.get_detail(entry_id)
        self.entries.remove(entry)


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
        request.session["employee_id"] = 1 if role is EmployeeRole.ADMIN else 2
        request.session["employee_role"] = role.value
        request.session["admin_id"] = 1
        request.session["admin_session_version"] = 1
        request.session["last_activity_at"] = datetime.now(UTC).isoformat()
        return {"ok": True}

    @app.post("/test/clear-knowledge-csrf")
    async def clear_knowledge_csrf(request: Request) -> dict[str, bool]:
        """模拟浏览器删改兼容字段，验证授权只依赖服务端 nonce。"""
        request.session.pop("knowledge_csrf", None)
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

    assert "/static/admin.js" in index.text
    assert 'href="/employee/knowledge" aria-current="page"' in detail.text
    assert 'action="/employee/knowledge/1/edit"' in detail.text
    assert "data-unsaved-warning" in detail.text
    assert 'action="/employee/knowledge/1/disable"' not in detail.text
    assert 'action="/employee/knowledge/1/disable"' in index.text
    assert 'action="/employee/knowledge/1/edit"' not in staff_detail.text
    assert "下午三点后" in staff_detail.text
    assert missing.status_code == 404


def test_knowledge_csrf_tokens_survive_navigation_and_remain_single_use() -> None:
    """列表和详情签发的令牌应并存，且各自仍只能成功使用一次。"""
    client, service = build_client(EmployeeRole.ADMIN)
    index = client.get("/employee/knowledge")
    index_token = re.search(r'name="csrf_token" value="([^"]+)"', index.text).group(1)
    detail = client.get("/employee/knowledge/1")
    detail_token = re.search(r'name="csrf_token" value="([^"]+)"', detail.text).group(1)

    created = client.post(
        "/employee/knowledge",
        data={
            "scope": "global",
            "category": "交通",
            "question_zh": "怎么到民宿？",
            "answer_zh": "请按导航前往。",
            "question_en": "How can I get there?",
            "answer_en": "Please follow the map.",
            "keywords": "交通",
            "csrf_token": index_token,
        },
        follow_redirects=False,
    )
    replayed = client.post(
        "/employee/knowledge",
        data={
            "category": "重放",
            "question_zh": "重放",
            "answer_zh": "重放",
            "question_en": "Replay",
            "answer_en": "Replay",
            "keywords": "",
            "csrf_token": index_token,
        },
        follow_redirects=False,
    )
    edited = client.post(
        "/employee/knowledge/1/edit",
        data={
            "scope": "global",
            "category": "入住",
            "question_zh": "几点可以入住？",
            "answer_zh": "下午三点后。",
            "question_en": "When is check-in?",
            "answer_en": "After 3 PM.",
            "keywords": "入住",
            "csrf_token": detail_token,
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert replayed.status_code == 409
    assert edited.status_code == 303
    assert service.entries[0].question_zh == "几点可以入住？"


def test_knowledge_csrf_token_collection_is_bounded() -> None:
    """连续打开页面时只保留最近八个令牌，避免会话无限增长。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    tokens = []
    for _ in range(9):
        response = client.get("/employee/knowledge")
        tokens.append(re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1))

    oldest = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": tokens[0]},
    )
    newest = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": tokens[-1]},
    )

    assert oldest.status_code == 409
    assert newest.status_code == 200


def test_knowledge_csrf_survives_interleaved_get_cookie_updates() -> None:
    """两个页面从同一旧 Cookie 签发时，先返回页面的 nonce 仍必须有效。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    original_cookies = dict(client.cookies)

    client.cookies.clear()
    client.cookies.update(original_cookies)
    index = client.get("/employee/knowledge")
    index_token = re.search(r'name="csrf_token" value="([^"]+)"', index.text).group(1)

    # 模拟详情 GET 与列表 GET 同时读取签发前的同一份 Cookie，且详情响应最后落盘。
    client.cookies.clear()
    client.cookies.update(original_cookies)
    client.get("/employee/knowledge/1")
    submitted = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": index_token},
    )

    assert submitted.status_code == 200


def test_knowledge_csrf_is_atomically_consumed_across_same_cookie_posts() -> None:
    """两个 POST 复用同一旧 Cookie 和 nonce 时，服务端只能接受其中一个。"""
    client, service = build_client(EmployeeRole.ADMIN)
    page = client.get("/employee/knowledge")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    csrf_service = client.app.state.admin_csrf_service
    assert csrf_service.pending[token] == ("knowledge-write", 1)
    unconsumed_cookies = dict(client.cookies)
    payload = {
        "scope": "global",
        "category": "交通",
        "question_zh": "怎么到民宿？",
        "answer_zh": "请按导航前往。",
        "question_en": "How can I get there?",
        "answer_en": "Please follow the map.",
        "keywords": "交通",
        "csrf_token": token,
    }

    first = client.post("/employee/knowledge", data=payload, follow_redirects=False)
    # 恢复未消费 Cookie，模拟另一个并发请求已经携带同一份请求头发出。
    client.cookies.clear()
    client.cookies.update(unconsumed_cookies)
    second = client.post("/employee/knowledge", data=payload, follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 409
    assert len(service.entries) == 2


def test_knowledge_csrf_cookie_metadata_is_not_an_authorization_source() -> None:
    """删除 Cookie 内兼容集合后，服务端 nonce 仍应成功一次且只能成功一次。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    page = client.get("/employee/knowledge")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post("/test/clear-knowledge-csrf")

    first = client.post("/employee/knowledge/1/disable", data={"csrf_token": token})
    replay = client.post("/employee/knowledge/1/disable", data={"csrf_token": token})

    assert first.status_code == 200
    assert replay.status_code == 409


def test_admin_shell_navigation_is_trimmed_for_staff_role() -> None:
    """员工两套导航只显示其真实可访问页面，管理员仍保留全部入口。"""
    staff, _ = build_client(EmployeeRole.STAFF)
    admin, _ = build_client(EmployeeRole.ADMIN)

    staff_page = staff.get("/employee/knowledge")
    admin_page = admin.get("/employee/knowledge")

    for allowed in ("/employee/tasks", "/employee/knowledge", "/employee/account"):
        assert allowed in staff_page.text
    for forbidden in (
        "/employee/admin",
        "/employee/properties",
        "/employee/customers",
        "/employee/approvals",
        "/employee/admin/diagnostics",
    ):
        assert forbidden not in staff_page.text
        assert forbidden in admin_page.text


def test_knowledge_index_defaults_to_entries_and_splits_candidates() -> None:
    """AC11：默认只显示现有知识，候选在独立分区；两个分区互不加载对方。"""
    client, service = build_client(EmployeeRole.ADMIN)

    entries_page = client.get("/employee/knowledge")
    assert entries_page.status_code == 200
    assert 'id="knowledge-entries"' in entries_page.text
    assert "新增知识条目" in entries_page.text
    assert "能停车吗" not in entries_page.text
    assert service.list_candidate_calls == []
    assert 'action="/employee/knowledge/1/disable" data-confirm=' in entries_page.text

    candidates_page = client.get("/employee/knowledge?view=candidates")
    assert candidates_page.status_code == 200
    assert "能停车吗" in candidates_page.text
    assert 'id="knowledge-entries"' not in candidates_page.text
    assert service.list_all_calls == [(0, 51)]
    assert 'aria-current="page">待审核候选' in candidates_page.text


def test_staff_never_sees_the_candidate_partition() -> None:
    """普通员工带 view=candidates 也只看到现有知识，不加载候选。"""
    client, service = build_client(EmployeeRole.STAFF)

    response = client.get("/employee/knowledge?view=candidates")

    assert response.status_code == 200
    assert 'id="knowledge-entries"' in response.text
    assert "待审核候选" not in response.text
    assert service.list_candidate_calls == []


def test_knowledge_lists_use_independent_bounded_pagination() -> None:
    """正式知识和候选分别分页，翻页链接保留分区与彼此页码。"""
    client, service = build_client(EmployeeRole.ADMIN)

    entries = client.get("/employee/knowledge?page=2&candidate_page=2")
    candidates = client.get("/employee/knowledge?view=candidates&page=2&candidate_page=2")

    assert entries.status_code == 200 and candidates.status_code == 200
    assert service.list_all_calls == [(50, 51)]
    assert service.list_candidate_calls == [(50, 51)]
    assert 'href="/employee/knowledge?page=1&amp;candidate_page=2"' in entries.text
    assert 'href="/employee/knowledge?page=3&amp;candidate_page=2"' in entries.text
    assert (
        'href="/employee/knowledge?page=2&amp;candidate_page=1&amp;view=candidates"'
        in candidates.text
    )
    assert (
        'href="/employee/knowledge?page=2&amp;candidate_page=3&amp;view=candidates"'
        in candidates.text
    )


def test_invalid_partition_is_rejected_not_blank() -> None:
    """分区参数只接受两个取值，错误值稳定拒绝，不渲染一个空白页。"""
    client, _ = build_client(EmployeeRole.ADMIN)

    assert client.get("/employee/knowledge?view=unknown").status_code == 422


def test_knowledge_filter_form_accepts_empty_enabled_value() -> None:
    """只搜索知识时，原生表单附带的空启用状态应按未筛选处理。"""
    client, _ = build_client(EmployeeRole.ADMIN)

    response = client.get(
        "/employee/knowledge",
        params={"query": "入住", "enabled": "", "category": ""},
    )

    assert response.status_code == 200
    assert "几点入住" in response.text
    assert "data-filter-form" in response.text
    assert client.get("/employee/knowledge?enabled=unknown").status_code == 422


@pytest.mark.asyncio
async def test_disabling_knowledge_removes_it_from_bot_context() -> None:
    """停用内容必须立即退出机器人可用上下文。"""
    client, repository = build_client(EmployeeRole.ADMIN)
    knowledge_service = KnowledgeService(repository)

    page = client.get("/employee/knowledge")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": csrf_token},
    )
    context = await knowledge_service.retrieve(Language.ZH, "入住时间")

    assert response.status_code == 200
    assert 1 not in {item.source_id for item in context}


def test_admin_can_create_bilingual_knowledge() -> None:
    """管理员新增时必须同时提交中英文问答。"""
    client, service = build_client(EmployeeRole.ADMIN)

    page = client.get("/employee/knowledge")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/employee/knowledge",
        data={
            "scope": "global",
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

    page = client.get("/employee/knowledge?view=candidates")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert "待审核候选" in page.text
    assert "能停车吗" in page.text
    assert "停车位置和收费规则" in page.text

    response = client.post(
        "/employee/knowledge/candidates/8/convert",
        data={
            "scope": "global",
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
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
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

        userids = await SQLAlchemyEmployeeRepository(session).list_active_admin_userids()

        assert userids == ["admin-active"]

    await engine.dispose()


KNOWLEDGE_SOURCE = "/employee/knowledge?query=退房&enabled=disabled&page=3&candidate_page=2"


def test_knowledge_actions_return_to_the_view_they_came_from() -> None:
    """整理知识时每操作一条就被丢回第一页，两个列表的位置都会丢。

    列表支持 query、enabled、category、page、candidate_page 五个参数，但四个
    写操作全部返回裸列表，表单也不带来源。
    """
    client, _ = build_client(EmployeeRole.ADMIN)
    page = client.get(KNOWLEDGE_SOURCE)
    assert 'name="return_to"' in page.text

    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": token, "return_to": KNOWLEDGE_SOURCE},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/employee/knowledge?")
    assert "page=3" in response.headers["location"]
    assert "candidate_page=2" in response.headers["location"]


def test_knowledge_actions_refuse_a_foreign_source() -> None:
    """站外 return_to 必须回落到知识列表。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    page = client.get("/employee/knowledge")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": token, "return_to": "https://evil.example.com"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "evil.example.com" not in response.headers["location"]
    assert response.headers["location"].startswith("/employee/knowledge")


def test_knowledge_scope_form_rejects_roomless_property_and_invalid_dates():
    """范围和日期错误在写入前给出明确错误，不保存错误知识。"""
    client, service = build_client(EmployeeRole.ADMIN)
    for metadata in (
        {"scope": "property"},
        {"scope": "global", "property_id": "201"},
        {"scope": "global", "valid_from": "2026-09-26", "valid_until": "2026-09-25"},
    ):
        page = client.get("/employee/knowledge")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        result = client.post(
            "/employee/knowledge",
            data={
                "category": "早餐",
                "question_zh": "早餐？",
                "answer_zh": "7点",
                "question_en": "Breakfast?",
                "answer_en": "At 7",
                "csrf_token": token,
                **metadata,
            },
        )
        assert result.status_code == 422
    assert len(service.entries) == 1


def test_knowledge_scope_dates_survive_form_save_and_edit_render():
    """范围及日期随表单保存，编辑页回显相同选择，不误重置审核范围。"""
    client, service = build_client(EmployeeRole.ADMIN)
    page = client.get("/employee/knowledge/1")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/employee/knowledge/1/edit",
        data={
            "category": "早餐",
            "question_zh": "早餐？",
            "answer_zh": "7点",
            "question_en": "Breakfast?",
            "answer_en": "At 7",
            "csrf_token": token,
            "scope": "global",
            "valid_from": "2026-09-25",
            "valid_until": "2026-09-30",
        },
    )
    assert response.status_code == 200
    assert service.entries[0].scope == "global"
    assert service.entries[0].valid_from == date(2026, 9, 25)
    assert service.entries[0].valid_until == date(2026, 9, 30)
    rendered = client.get("/employee/knowledge/1").text
    assert 'value="global" selected' in rendered
    assert 'name="valid_from" value="2026-09-25"' in rendered
    assert 'name="valid_until" value="2026-09-30"' in rendered


def _token(html: str) -> str:
    """从页面取出一次性表单令牌。"""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


_HTML = {"accept": "text/html"}
_BAD_CREATE = {
    "category": "早餐",
    "question_zh": "早餐几点？",
    "answer_zh": "七点到九点，餐厅在一楼。",
    "question_en": "Breakfast?",
    "answer_en": "7 to 9 AM.",
    "scope": "property",
    "property_id": "",
}


def test_create_error_rerenders_list_with_input_and_a_fresh_token() -> None:
    """AC03：新建时范围填错，页面原地显示错误并保留已填内容；新令牌可纠正后成功。"""
    client, service = build_client(EmployeeRole.ADMIN)
    token = _token(client.get("/employee/knowledge?page=1").text)

    failed = client.post(
        "/employee/knowledge",
        data={**_BAD_CREATE, "csrf_token": token, "return_to": "/employee/knowledge?page=1"},
        headers=_HTML,
    )

    assert failed.status_code == 422
    assert failed.headers["content-type"].startswith("text/html")
    assert "知识适用范围或日期有误" in failed.text
    assert "七点到九点，餐厅在一楼。" in failed.text
    assert 'id="knowledge-create" open' in failed.text
    assert len(service.entries) == 1
    # 旧令牌已被消费，重放仍被拒。
    replay = client.post(
        "/employee/knowledge", data={**_BAD_CREATE, "csrf_token": token}, headers=_HTML
    )
    assert replay.status_code == 409

    fixed = client.post(
        "/employee/knowledge",
        data={**_BAD_CREATE, "scope": "global", "csrf_token": _token(failed.text)},
        headers=_HTML,
        follow_redirects=False,
    )
    assert fixed.status_code == 303
    assert len(service.entries) == 2


def test_api_create_error_keeps_json_422() -> None:
    """接口调用不带 text/html 时仍得到原来的 422 JSON。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    token = _token(client.get("/employee/knowledge").text)

    response = client.post("/employee/knowledge", data={**_BAD_CREATE, "csrf_token": token})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")


def test_edit_error_rerenders_detail_with_submitted_text() -> None:
    """详情页编辑出错时回填刚提交的内容，而不是库里的旧值。"""
    client, service = build_client(EmployeeRole.ADMIN)
    token = _token(client.get("/employee/knowledge/1").text)

    response = client.post(
        "/employee/knowledge/1/edit",
        data={**_BAD_CREATE, "answer_zh": "改过但没保存的答案", "csrf_token": token},
        headers=_HTML,
    )

    assert response.status_code == 422
    assert "改过但没保存的答案" in response.text
    assert "已保留你刚才填写的内容" in response.text
    assert service.entries[0].answer_zh == "下午三点后。"


def test_candidate_convert_error_reopens_that_candidate_with_input() -> None:
    """候选转换出错时回到候选分区，展开出错的那一项并回填内容。"""
    client, service = build_client(EmployeeRole.ADMIN)
    token = _token(client.get("/employee/knowledge?view=candidates").text)

    response = client.post(
        "/employee/knowledge/candidates/8/convert",
        data={
            **_BAD_CREATE,
            "answer_zh": "停车在院外",
            "csrf_token": token,
            "return_to": "/employee/knowledge?view=candidates",
        },
        headers=_HTML,
    )

    assert response.status_code == 422
    assert "停车在院外" in response.text
    assert '<details class="collapsible-section" open>' in response.text
    assert service.converted is None


def test_delete_reaches_its_handler_and_returns_to_the_source_view() -> None:
    """AC08：删除请求经真实路由表到达删除处理器（不被启停路由截走），回到来源并提示。"""
    client, service = build_client(EmployeeRole.ADMIN)
    source = "/employee/knowledge?page=1&enabled=enabled"
    token = _token(client.get(source).text)

    response = client.post(
        "/employee/knowledge/1/delete",
        data={"csrf_token": token, "return_to": source},
        headers=_HTML,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == source
    assert service.entries == []
    assert "已永久删除知识 #1" in client.get(source, headers=_HTML).text


def test_delete_from_detail_page_lands_on_the_list() -> None:
    """从详情页删除后详情已不存在，改回知识列表而不是 404。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    token = _token(client.get("/employee/knowledge/1").text)

    response = client.post(
        "/employee/knowledge/1/delete",
        data={"csrf_token": token, "return_to": "/employee/knowledge/1"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/employee/knowledge"


def test_staff_cannot_delete_and_missing_entry_is_404() -> None:
    """普通员工伪造删除被拒；重复删除得到稳定的 404，没有副作用。"""
    staff, staff_service = build_client(EmployeeRole.STAFF)
    assert staff.post("/employee/knowledge/1/delete", data={"csrf_token": "x"}).status_code == 403
    assert len(staff_service.entries) == 1

    admin, _ = build_client(EmployeeRole.ADMIN)
    first = _token(admin.get("/employee/knowledge").text)
    admin.post("/employee/knowledge/1/delete", data={"csrf_token": first})
    second = _token(admin.get("/employee/knowledge").text)
    again = admin.post("/employee/knowledge/1/delete", data={"csrf_token": second})
    assert again.status_code == 404


def test_detail_link_and_back_button_keep_the_source_view() -> None:
    """F10：从带筛选的列表进详情，返回按钮和保存都回到原列表。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    source = "/employee/knowledge?page=1&candidate_page=1&enabled=enabled"

    listing = client.get("/employee/knowledge?enabled=enabled").text
    assert "/employee/knowledge/1?return_to=/employee/knowledge%3Fpage%3D1" in listing

    detail = client.get("/employee/knowledge/1", params={"return_to": source}).text
    assert f'href="{source.replace("&", "&amp;")}"' in detail
    assert f'name="return_to" value="{source.replace("&", "&amp;")}"' in detail

    foreign = client.get("/employee/knowledge/1", params={"return_to": "https://evil.example"})
    assert 'href="/employee/knowledge">返回知识列表' in foreign.text


def test_unreviewed_enabled_entry_is_not_shown_as_plain_enabled() -> None:
    """AC04：启用但范围待审核的条目明确标出「暂不参与回答」。"""
    client, service = build_client(EmployeeRole.ADMIN)
    service.entries[0].scope = "unreviewed"

    text = client.get("/employee/knowledge").text

    assert "范围待审核，暂不参与回答" in text
