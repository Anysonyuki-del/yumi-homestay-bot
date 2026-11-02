"""小程序公开内容 HTTP 集成：真实内容服务、临时 SQLite，经与 main.py 相同的中间件访问。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import Base, Employee, KnowledgeEntry
from homestay_bot.middleware import AdminNoStoreMiddleware
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.routes.miniprogram_content import router as content_router
from homestay_bot.services import miniprogram_content
from homestay_bot.services.miniprogram_content import MiniProgramContentService
from homestay_bot.services.private_file_storage import PrivateFileStorage

TODAY = date(2026, 11, 3)


@dataclass
class Harness:
    """一次测试的应用与数据库。"""

    app: FastAPI
    factory: async_sessionmaker[AsyncSession]

    def client(self) -> httpx.AsyncClient:
        """返回直连 ASGI 应用的 HTTP 客户端。"""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://testserver",
        )

    async def add_entry(self, **overrides: Any) -> KnowledgeEntry:
        """写入一条默认满足全部展示条件的知识，按需覆盖单个字段做反例。"""
        fields: dict[str, Any] = {
            "category": "入住",
            "question_zh": "几点入住？",
            "answer_zh": "下午三点后。",
            "question_en": "Check-in time?",
            "answer_en": "After 3 PM.",
            "keywords": ["入住"],
            "trigger_any": ["内部条件词"],
            "scope": "global",
            "is_enabled": True,
            "show_in_miniprogram": True,
        }
        fields.update(overrides)
        async with self.factory() as session:
            entry = KnowledgeEntry(**fields)
            session.add(entry)
            await session.commit()
            return entry


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    """创建临时 SQLite 与装配好内容服务的应用。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'content.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    app = FastAPI()
    app.add_middleware(AdminNoStoreMiddleware)
    app.include_router(content_router)
    app.state.miniprogram_content_service = MiniProgramContentService(
        factory, PrivateFileStorage(tmp_path / "uploads"), today=lambda: TODAY
    )
    yield Harness(app=app, factory=factory)
    await engine.dispose()


async def _guide(harness: Harness) -> dict[str, Any]:
    """请求入住指南并断言成功。"""
    async with harness.client() as client:
        response = await client.get("/api/miniprogram/guide")
    assert response.status_code == 200
    return response.json()


def _questions(body: dict[str, Any]) -> list[str]:
    """把分组结果摊平成问题列表。"""
    return [item["question"] for section in body["sections"] for item in section["items"]]


async def test_guide_returns_only_whitelisted_fields(harness: Harness) -> None:
    """只输出分类、中文问题和答案；英文、关键词、条件词和编号都不外露。"""
    await harness.add_entry()
    body = await _guide(harness)
    assert body == {
        "sections": [
            {"category": "入住", "items": [{"question": "几点入住？", "answer": "下午三点后。"}]}
        ]
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"show_in_miniprogram": False},
        {"is_enabled": False},
        {"scope": "unreviewed"},
        {"scope": "public"},
        {"valid_from": date(2026, 11, 4)},
        {"valid_until": date(2026, 11, 2)},
    ],
    ids=["not-ticked", "disabled", "unreviewed", "public", "starts-tomorrow", "ended-yesterday"],
)
async def test_guide_excludes_each_failing_condition(
    harness: Harness, overrides: dict[str, Any]
) -> None:
    """展示条件逐项取反，任何一项不满足都不出现。"""
    await harness.add_entry(question_zh="应展示")
    await harness.add_entry(question_zh="不应展示", **overrides)
    assert _questions(await _guide(harness)) == ["应展示"]


async def test_guide_excludes_room_specific_knowledge(harness: Harness) -> None:
    """指定房间的知识可能含房间专属信息，即使勾选也不出现。"""
    from homestay_bot.domain.models import PropertyProfile

    async with harness.factory() as session:
        session.add(PropertyProfile(id=101, title="山景房"))
        await session.commit()
    await harness.add_entry(question_zh="房间专属", scope="property", property_id=101)
    assert _questions(await _guide(harness)) == []


async def test_guide_validity_bounds_are_inclusive(harness: Harness) -> None:
    """有效期起止当天都算有效。"""
    await harness.add_entry(question_zh="今天开始", valid_from=TODAY)
    await harness.add_entry(question_zh="今天结束", valid_until=TODAY)
    assert _questions(await _guide(harness)) == ["今天开始", "今天结束"]


async def test_guide_groups_by_first_appearance_of_category(harness: Harness) -> None:
    """分类按其最早条目排序，组内按编号排序，不按分类名排序。"""
    await harness.add_entry(category="设施", question_zh="设施1")
    await harness.add_entry(category="入住", question_zh="入住1")
    await harness.add_entry(category="设施", question_zh="设施2")
    body = await _guide(harness)
    assert [section["category"] for section in body["sections"]] == ["设施", "入住"]
    assert [item["question"] for item in body["sections"][0]["items"]] == ["设施1", "设施2"]


async def test_guide_is_capped(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过上限只截断，不报错；截掉的是编号最大的条目。"""
    monkeypatch.setattr(miniprogram_content, "GUIDE_MAX_ITEMS", 2)
    for index in range(3):
        await harness.add_entry(question_zh=f"问题{index}")
    assert _questions(await _guide(harness)) == ["问题0", "问题1"]


async def test_guide_service_failure_returns_fixed_message(harness: Harness) -> None:
    """服务故障时返回固定文案，不回显异常内容。"""

    class Broken:
        async def guide(self) -> list[object]:
            raise RuntimeError("SELECT secret FROM somewhere")

    harness.app.state.miniprogram_content_service = Broken()
    async with harness.client() as client:
        response = await client.get("/api/miniprogram/guide")
    assert response.status_code == 503
    assert "secret" not in response.text


async def test_admin_update_toggles_miniprogram_visibility(harness: Harness) -> None:
    """后台编辑保存时开关真正写库：勾选后出现在指南，取消后消失。"""
    entry = await harness.add_entry(show_in_miniprogram=False)
    async with harness.factory() as session:
        admin = Employee(wecom_userid="admin-1", name="管理员", role=EmployeeRole.ADMIN)
        session.add(admin)
        await session.commit()
        admin_id = admin.id
    for shown, expected in ((True, ["几点入住？"]), (False, [])):
        async with harness.factory() as session:
            await KnowledgeAdminService(session).update(
                entry.id, admin_id, show_in_miniprogram=shown
            )
        assert _questions(await _guide(harness)) == expected
