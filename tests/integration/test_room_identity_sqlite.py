"""房号身份解析走生产仓储（房号身份 Spec §2.4）：临时 SQLite、真实检索与房源资料。

单元替身按映射表给房源，这里用生产 `SessionKnowledgeRepository.list_room_profiles` 读
property_profiles，证明带楼栋的 room_number、只靠标题的房源和同号异栋都按同一规则解析。
"""

import pytest
import pytest_asyncio

from homestay_bot.application import SessionKnowledgeRepository
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import Base, KnowledgeEntry, PropertyProfile
from homestay_bot.integrations.deepseek_client import AssistantRequestContext
from homestay_bot.services.knowledge_service import KnowledgeService
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import _assistant, _decision, _respond


@pytest_asyncio.fixture
async def factory(tmp_path):
    """项目真实引擎的临时文件库，写入三种房源与各自的房间资料。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'rooms.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_session_factory(engine)
    async with sessions() as session:
        session.add_all([
            PropertyProfile(id=2303, room_number="2栋2303", title="武汉江山《挽江》2栋2303"),
            PropertyProfile(id=1803, room_number="1栋1803", title="《丹麦》1栋1803"),
            PropertyProfile(id=2803, room_number=None, title="《古家》2栋1803"),
            PropertyProfile(id=9303, room_number="2303", title="停用房", is_active=False),
        ])
        # 知识条目外键指向房源，先提交房源。
        await session.commit()
        for entry_id, property_id, category, question, answer in [
            (1, 2303, "停车", "停车收费吗？", "2303停车3元/小时，每天40元封顶。"),
            (2, 1803, "卫浴", "房间有浴缸吗？", "1栋1803没有浴缸，只有淋浴。"),
            (3, 2803, "卫浴", "房间有浴缸吗？", "2栋1803有独立浴缸。"),
        ]:
            session.add(KnowledgeEntry(
                id=entry_id, category=category, scope="property", property_id=property_id,
                is_enabled=True, question_zh=question, answer_zh=answer,
                question_en=question, answer_en=answer, keywords=[category],
            ))
        await session.commit()
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _ask(factory, text: str, fields: dict, conversation: int | None = None):
    """真实知识服务与房源资料，合成规划与主模型。"""
    assistant, _ = _assistant(
        [_decision()], KnowledgeService(SessionKnowledgeRepository(factory))
    )
    kwargs = (
        {"request_context": AssistantRequestContext(property_id=conversation)}
        if conversation else {}
    )
    return await _respond(
        assistant, text, plan_for(text, ("static_fact", text, fields)), **kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"target_room": "2303"}])
async def test_bare_number_matches_room_number_with_building(factory, fields):
    """生产形态 room_number「2栋2303」：客人写「2303」，规划填没填都取到该房资料（E3）。"""
    decision = await _ask(factory, "2303停车怎么收费", fields)
    assert "3元/小时" in decision.reply_text
    assert not decision.gap_units


@pytest.mark.asyncio
async def test_title_only_room_and_building_disambiguation(factory):
    """room_number 为空的房源靠标题兜底；写了楼栋只取该栋，不混入同号另一栋。"""
    decision = await _ask(factory, "2栋1803有浴缸吗", {})
    assert "独立浴缸" in decision.reply_text and "只有淋浴" not in decision.reply_text


@pytest.mark.asyncio
async def test_same_number_two_buildings(factory):
    """同号两栋：本轮房源在其中时选它；没有本轮房源时不作答、不转交（D-R2）。"""
    chosen = await _ask(factory, "1803有浴缸吗", {}, conversation=1803)
    assert "只有淋浴" in chosen.reply_text
    unresolved = await _ask(factory, "1803有浴缸吗", {})
    assert "浴缸" not in unresolved.reply_text.replace("尚未确认", "")
    assert [gap.reason for gap in unresolved.gap_units] == ["scope_unbound"]
