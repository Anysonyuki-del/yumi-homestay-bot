from dataclasses import dataclass

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole, Language
from homestay_bot.domain.models import AuditLog, Base, Employee
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


class KnowledgeAdminStub:
    """在内存中实现管理页和机器人共享的知识源。"""

    def __init__(self) -> None:
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

    async def list_all(self) -> list[EntryStub]:
        """返回全部条目供管理页展示。"""
        return self.entries

    async def list_active(self) -> list[EntryStub]:
        """只返回启用条目供机器人使用。"""
        return [entry for entry in self.entries if entry.is_enabled]

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


def build_client(
    role: EmployeeRole,
) -> tuple[TestClient, KnowledgeAdminStub]:
    """创建带测试登录入口的知识管理应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.include_router(knowledge_router)
    service = KnowledgeAdminStub()
    app.state.knowledge_admin_service = service

    @app.post("/test/login")
    async def test_login(request: Request) -> dict[str, bool]:
        """仅在测试应用中写入可信员工会话。"""
        request.session["employee_id"] = 1
        request.session["employee_role"] = role.value
        return {"ok": True}

    client = TestClient(app)
    client.post("/test/login")
    return client, service


def test_regular_customer_service_can_read_but_cannot_modify() -> None:
    """普通客服可查看知识，但所有修改接口均应拒绝。"""
    client, _ = build_client(EmployeeRole.CUSTOMER_SERVICE)

    detail = client.get("/employee/knowledge")
    disable = client.post("/employee/knowledge/1/disable")

    assert detail.status_code == 200
    assert "几点入住" in detail.text
    assert disable.status_code == 403


@pytest.mark.asyncio
async def test_disabling_knowledge_removes_it_from_bot_context() -> None:
    """停用内容必须立即退出机器人可用上下文。"""
    client, repository = build_client(EmployeeRole.ADMIN)
    knowledge_service = KnowledgeService(repository)

    response = client.post("/employee/knowledge/1/disable")
    context = await knowledge_service.build_context(Language.ZH)

    assert response.status_code == 204
    assert 1 not in {item.source_id for item in context}


def test_admin_can_create_bilingual_knowledge() -> None:
    """管理员新增时必须同时提交中英文问答。"""
    client, service = build_client(EmployeeRole.ADMIN)

    response = client.post(
        "/employee/knowledge",
        data={
            "category": "交通",
            "question_zh": "怎么到民宿？",
            "answer_zh": "请按导航前往。",
            "question_en": "How can I get there?",
            "answer_en": "Please follow the map.",
            "keywords": "交通,导航",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert service.entries[-1].question_en == "How can I get there?"


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
