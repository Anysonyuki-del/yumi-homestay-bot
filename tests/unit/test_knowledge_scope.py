from datetime import date
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.knowledge_service import KnowledgeService


@pytest.mark.asyncio
async def test_scope_and_dates_filter_before_semantic_ranking():
    """房间、审核与日期边界先于召回，语义排序不能重新引入排除项。"""
    rows = []
    for index, metadata in enumerate([
        {"scope": "global"},
        {"scope": "property", "property_id": 201},
        {"scope": "property", "property_id": 202},
        {"scope": "unreviewed"},
        {"scope": "global", "valid_until": date(2026, 9, 24)},
        {"scope": "global", "valid_from": date(2026, 9, 26)},
        {"scope": "public"},
    ], 1):
        fields = dict(id=index, category="早餐", question_zh="早餐时间", answer_zh="7点早餐",
                      question_en="Breakfast", answer_en="Breakfast at 7", keywords=[],
                      property_id=None, valid_from=None, valid_until=None)
        rows.append(SimpleNamespace(**(fields | metadata)))

    class Repository:
        async def list_active(self):
            """返回测试知识。"""
            return rows

    class Semantic:
        async def rank(self, language, query, entries):
            """断言语义层只收到允许的证据，再故意返回全部编号。"""
            assert {entry.id for entry in entries} == {1, 2, 7}
            return list(range(1, 8))

    result = await KnowledgeService(Repository(), Semantic()).retrieve(
        Language.ZH, "早餐", property_id=201, target_date=date(2026, 9, 25)
    )
    assert {item.source_id for item in result} == {1, 2, 7}
    assert next(item for item in result if item.source_id == 2).property_id == 201


@pytest.mark.parametrize("scope,room,start,end", [
    ("bad", None, None, None),
    ("property", None, None, None),
    ("global", 201, None, None),
    ("property", -1, None, None),
    ("public", None, date(2026, 9, 26), date(2026, 9, 25)),
])
def test_scope_write_validation(scope, room, start, end):
    """管理、导入入口共享同一范围和时序校验。"""
    from homestay_bot.services.knowledge_service import validate_knowledge_scope

    with pytest.raises(ValueError):
        validate_knowledge_scope(scope, room, start, end)


@pytest.mark.asyncio
async def test_date_range_retains_each_overlapping_policy_boundary():
    """跨生效区间保留各段原始日期，供回复分别说明，不能丢成全程无资料。"""
    class Repository:
        async def list_active(self):
            """提供连续两段早餐政策及区间外资料。"""
            return [SimpleNamespace(
                id=index, category="早餐", question_zh="早餐时间", answer_zh=answer,
                question_en="Breakfast", answer_en="Breakfast", keywords=[],
                scope="global", property_id=None, valid_from=start, valid_until=end,
            ) for index, answer, start, end in [
                (1, "7点开始", date(2026, 9, 1), date(2026, 9, 25)),
                (2, "8点开始", date(2026, 9, 26), date(2026, 9, 30)),
                (3, "9点开始", date(2026, 10, 1), None),
            ]]

    result = await KnowledgeService(Repository()).retrieve(
        Language.ZH, "早餐", target_date=date(2026, 9, 25),
        target_end_date=date(2026, 9, 27),
    )
    assert {item.source_id for item in result} == {1, 2}
    assert next(item for item in result if item.source_id == 1).valid_until == date(2026, 9, 25)
    assert next(item for item in result if item.source_id == 2).valid_from == date(2026, 9, 26)
