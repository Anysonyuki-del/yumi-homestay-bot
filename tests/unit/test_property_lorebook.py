"""房间知识「世界书化」（Spec 2026-09-28 F2–F6）的回归：只用合成数据，不访问外部服务。"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import Base, KnowledgeEntry, PropertyProfile
from homestay_bot.services.knowledge_evidence_policy import carry_followup_topic
from homestay_bot.services.knowledge_service import (
    KnowledgeService,
    KnowledgeSnippet,
    PropertyCard,
)


@dataclass
class Row:
    """知识服务读取的合成知识行。"""

    id: int
    category: str
    question_zh: str
    answer_zh: str
    question_en: str = "Q?"
    answer_en: str = "A."
    keywords: list[str] = field(default_factory=list)
    scope: str = "global"
    property_id: int | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    trigger_any: list[str] | None = None
    trigger_exclude: list[str] | None = None


class CardRepository:
    """带房源卡片的只读知识仓储替身。"""

    def __init__(self, rows: list[Row], card: PropertyCard | None, fail: bool = False) -> None:
        """保存知识行与卡片；fail 模拟卡片读取异常。"""
        self.rows, self.card, self.fail = rows, card, fail

    async def list_active(self) -> list[Row]:
        """返回全部知识行。"""
        return self.rows

    async def get_property_card(self, property_id: int) -> PropertyCard | None:
        """返回卡片或抛出读取异常。"""
        if self.fail:
            raise RuntimeError("synthetic failure")
        return self.card if self.card and self.card.property_id == property_id else None


PARKING = Row(1, "停车", "开车来停哪里？", "可以停地下一层。", keywords=["停车"])
CARD = PropertyCard(7, "合成江景房", address_hint="合成小区2栋23层", parking_instructions="B1 A区")


def retrieve(repository, question: str, property_id: int | None = 7):
    """按指定房间检索一次，返回片段列表。"""
    return asyncio.run(
        KnowledgeService(repository).retrieve(Language.ZH, question, property_id=property_id)
    )


def test_property_card_is_the_first_snippet_when_the_room_is_known() -> None:
    """F2：确定了房间就把房源卡片放在首位，编号为负的房源编号，房间未知时不附。"""
    found = retrieve(CardRepository([PARKING], CARD), "开车停哪里")
    assert found[0].source_id == -7 and found[0].scope == "property"
    assert "地址与楼层：合成小区2栋23层" in found[0].answer
    assert "停车：B1 A区" in found[0].answer
    assert [item.source_id for item in found[1:]] == [1]
    unknown_room = retrieve(CardRepository([PARKING], CARD), "停车", None)
    assert all(item.source_id != -7 for item in unknown_room)


def test_property_card_is_skipped_when_empty_or_failing() -> None:
    """F2：卡片除房名外没内容时不附；读取异常只记日志，知识检索照常。"""
    empty = PropertyCard(7, "只有房名")
    assert [item.source_id for item in retrieve(CardRepository([PARKING], empty), "停车")] == [1]
    failing = CardRepository([PARKING], CARD, fail=True)
    assert [item.source_id for item in retrieve(failing, "停车")] == [1]


def test_followup_borrows_the_most_recent_topic_from_all_earlier_messages() -> None:
    """F3：往前看这位客人的所有消息，借最近一次话题；新问题、回应和工具问题不借。"""
    history = ["停车场在哪", "你好", "早餐几点？", "谢谢"]
    assert carry_followup_topic("那要钱吗？", history) == "早餐：那要钱吗？"
    assert carry_followup_topic("It is free?", ["Do you have parking?"]).startswith("parking：")
    for unchanged in ("ok", "好的", "黄鹤楼在哪", "那302多少钱？", "武汉哪里好玩"):
        assert carry_followup_topic(unchanged, history) == unchanged
    assert carry_followup_topic("那要钱吗", []) == "那要钱吗"


def test_followup_answers_the_carried_topic_from_reviewed_knowledge() -> None:
    """F3：「早餐几点？」后问「那要钱吗」，按早餐的审核答案回答费用，模型答偏也不采用。"""
    from tests.unit.test_deepseek_client import ChatClientStub, decision_payload
    from tests.unit.test_reply_evidence_boundaries import Knowledge, make_assistant

    breakfast = "早餐7:30至9:30供应，每位28元，需前一晚预约。"
    client = ChatClientStub([json.dumps({**decision_payload(), "reply_text": "不用钱。"})])
    decision = asyncio.run(make_assistant(Knowledge([
        KnowledgeSnippet(1, "早餐", "早餐几点？收费吗？", breakfast, scope="global")
    ]), client=client).respond(
        guest_identifier="synthetic", language=Language.ZH,
        messages=[{"role": "user", "content": "那要钱吗？"}],
        guest_history=["早餐几点？"],
    ))
    assert "28元" in decision.reply_text and "不用钱" not in decision.reply_text


def test_trigger_words_filter_entries_before_ranking() -> None:
    """F5：排除词出现任一就不命中；附加条件词非空时至少出现其一才命中。"""
    rows = [
        Row(1, "停车", "开车来停哪里？", "可以停地下一层。", keywords=["停车"],
            trigger_exclude=["黄鹤楼"]),
        Row(2, "停车", "停车收费吗？", "3元/小时。", keywords=["停车"],
            trigger_any=["收费", "多少钱", "免费"]),
    ]
    def ids(question: str) -> list[int]:
        """检索并返回命中的知识编号。"""
        return [item.source_id for item in retrieve(CardRepository(rows, None), question, None)]

    # 条目 1 被排除词拦下；条目 2 缺附加条件词，也不命中。
    assert ids("黄鹤楼停车方便吗") == []
    assert ids("停车在哪") == [1]
    assert 2 in ids("停车收费吗")


@pytest.mark.asyncio
async def test_property_titles_follow_hostex_and_failures_do_not_break_reconcile() -> None:
    """F4：房名一律以百居易为准（覆盖人工修改、补建缺失房源）；同步失败不影响对账。"""
    from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
    from homestay_bot.services.hostex_sync import HostexSyncService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(PropertyProfile(id=11, title="员工改过的名字"))
        await session.flush()
        repo = SQLAlchemyOperationsRepository(session)
        changed = await repo.sync_property_titles([
            SimpleNamespace(id=11, title="合成《挽江》2栋2303"),
            SimpleNamespace(id=12, title="合成新房"),
            SimpleNamespace(id=13, title=""),
        ])
        assert changed == 2
        assert (await session.get(PropertyProfile, 11)).title == "合成《挽江》2栋2303"
        assert (await session.get(PropertyProfile, 12)).title == "合成新房"
        assert await session.get(PropertyProfile, 13) is None

        class Hostex:
            """订单为空、房源列表读取失败的百居易替身。"""

            async def list_reservations(self, query):
                """没有订单。"""
                return []

            async def list_properties(self):
                """模拟接口失败。"""
                raise RuntimeError("synthetic")

        assert await HostexSyncService(Hostex(), repo).reconcile(date.today(), date.today()) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_by_room_and_room_filter_in_admin_list(tmp_path) -> None:
    """F6：导入把房间算进身份，不同房间同问法不冲突；给了房间就按指定房间停用写入。"""
    from homestay_bot.domain.enums import EmployeeRole
    from homestay_bot.domain.models import Employee
    from homestay_bot.routes.knowledge import KnowledgeAdminService
    from homestay_bot.tools.import_knowledge_drafts import apply_import, parse_drafts, plan_import

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kb.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            Employee(id=1, wecom_userid="synthetic-admin", name="合成管理员",
                     role=EmployeeRole.ADMIN, is_active=True),
            PropertyProfile(id=1, title="合成甲房"), PropertyProfile(id=2, title="合成乙房"),
        ])
        await session.commit()

    def draft(property_id: int, answer: str) -> dict[str, object]:
        """同一问法、不同房间的草稿。"""
        return {"category": "地址交通", "question_zh": "地址在哪？", "answer_zh": answer,
                "question_en": "Address?", "answer_en": answer, "keywords": ["地址"],
                "is_enabled": False, "property_id": property_id,
                "trigger_exclude": ["黄鹤楼"]}

    drafts = parse_drafts(json.dumps([draft(1, "甲地址"), draft(2, "乙地址")]).encode())
    result = await apply_import(factory, drafts, admin_id=1)
    assert len(result.created_ids) == 2
    async with factory() as session:
        rows = [await session.get(KnowledgeEntry, entry_id) for entry_id in result.created_ids]
        assert [(r.scope, r.property_id, r.is_enabled) for r in rows] == [
            ("property", 1, False), ("property", 2, False)
        ]
        assert rows[0].trigger_exclude == ["黄鹤楼"]
        # 同一房间、同问法但答案不同才是冲突。
        clash = parse_drafts(json.dumps([draft(1, "甲新地址")]).encode())
        assert (await plan_import(session, clash)).conflicts == [1]
        listed = await KnowledgeAdminService(session).list_all(offset=0, limit=10, room="2")
        assert [row.answer_zh for row in listed] == ["乙地址"]
        shared = await KnowledgeAdminService(session).list_all(offset=0, limit=10, room="shared")
        assert shared == []
    await engine.dispose()


def test_service_fee_followup_is_not_treated_as_a_room_price_question() -> None:
    """F3：「Do you have parking?」后问「How much is it?」答停车费，不追问入住日期（MT-停车EN）。"""
    from tests.unit.test_deepseek_client import ChatClientStub, decision_payload
    from tests.unit.test_reply_evidence_boundaries import Knowledge, make_assistant

    parking = ("Parking at the synthetic car park costs 10 yuan per hour; "
               "free for the first 30 minutes.")
    client = ChatClientStub([json.dumps({
        **decision_payload(), "language": "en", "reply_text": "It is free.",
    })])
    decision = asyncio.run(make_assistant(Knowledge([
        KnowledgeSnippet(1, "停车", "Is parking free? How much?", parking, scope="global")
    ]), client=client).respond(
        guest_identifier="synthetic", language=Language.EN,
        messages=[
            {"role": "user", "content": "Do you have parking?"},
            {"role": "assistant", "content": "Yes, there is a car park nearby."},
            {"role": "user", "content": "How much is it?"},
        ],
    ))
    assert "Which dates" not in decision.reply_text
    assert "10 yuan" in decision.reply_text


def test_property_card_never_becomes_the_whole_fixed_answer() -> None:
    """房源卡片不能被当成整段固定回答发出去（2026-09-29 测试号实测：问停车回了整张卡片）。

    同话题有专门知识时用专门知识；只有卡片时只取与话题相关的那一行。
    """
    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
    from homestay_bot.services.knowledge_evidence_policy import build_evidence_plan
    from homestay_bot.services.knowledge_service import property_card_snippet

    card = property_card_snippet(PropertyCard(
        7, "合成江景房", room_type="合成套房", address_hint="合成小区2栋23层",
        parking_instructions="地下一层A区。收费以现场公示为准（目前3元/小时）。",
    ), Language.ZH)
    parking = KnowledgeSnippet(1, "停车", "开车来停哪里？", "停车场在地下一层A区，右转到电梯。",
                               scope="property", property_id=7)
    fee = KnowledgeSnippet(2, "停车", "停车收费吗？", "停车目前3元/小时，每日封顶40元。",
                           scope="property", property_id=7)

    def plan(question: str, knowledge: list) -> tuple[str, str]:
        """返回证据判定状态与固定回答正文。"""
        result = build_evidence_plan(
            question, knowledge, is_property_question=True,
            supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,
        )
        return result.status, "\n".join(result.answers)

    # 答案里顺带提到停车场的其他条目（门禁卡）不能抢在停车条目前面作答。
    access = KnowledgeSnippet(3, "客房设施", "门禁卡在哪里？",
                              "门禁卡在鞋柜上，可以进出停车场和一楼大门。",
                              scope="property", property_id=7)
    status, answer = plan("开车停哪里", [card, access, parking, fee])
    assert status == "grounded" and answer == "停车场在地下一层A区，右转到电梯。"
    status, answer = plan("停车：那要钱吗", [card, parking, fee])
    assert status == "grounded" and answer == "停车目前3元/小时，每日封顶40元。"
    status, answer = plan("开车停哪里", [card])
    assert status == "grounded"
    assert answer == "停车：地下一层A区。收费以现场公示为准（目前3元/小时）。"
    assert "合成小区" not in answer and "房型" not in answer


def test_registration_and_gate_questions_get_fixed_answers_and_are_not_followups() -> None:
    """2026-09-29 测试号实测：「到了小区门口怎么进」「怎么登记」认不出主题和「怎么做」属性，
    带图条目发不出；「怎么登记」还被当成追问，借用上文停车话题回了「尚未确认停车信息」。
    """
    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
    from homestay_bot.services.knowledge_evidence_policy import build_evidence_plan

    gate = KnowledgeSnippet(
        1, "地址交通", "到了小区大门怎么进？保安要登记吗？",
        "保安可能会给一份登记表，填写真实楼栋和房号即可。进门左边是1栋，右边是2栋。",
        scope="property", property_id=7,
    )
    registration = KnowledgeSnippet(
        2, "入住退房", "入住前要做什么登记？怎么拿到房间密码？",
        "入住前需要完成公安实名登记：扫描二维码，进入网约房登记系统填写身份信息。",
        scope="property", property_id=7,
    )
    parking = KnowledgeSnippet(3, "停车", "开车来停哪里？", "停车场在地下一层A区，右转到电梯。",
                               scope="property", property_id=7)
    fee = KnowledgeSnippet(4, "停车", "停车收费吗？", "停车目前3元/小时，每日封顶40元。",
                           scope="property", property_id=7)
    knowledge = [gate, registration, parking, fee]

    def chosen(question: str) -> tuple[str, list[str]]:
        """返回证据判定状态与作为固定回答的条目编号。"""
        result = build_evidence_plan(
            question, knowledge, is_property_question=True,
            supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,
        )
        return result.status, [part.evidence[0].source_id for part in result.parts]

    assert chosen("怎么登记") == ("grounded", ["2"])
    assert chosen("到了小区门口怎么进") == ("grounded", ["1"])
    assert chosen("How do I register?") == ("grounded", ["2"])
    # 「怎么收费」问的是费用，不当成办事流程再拼上停车位置条目。
    assert chosen("停车怎么收费") == ("grounded", ["4"])

    history = ["开车停哪里", "到了小区门口怎么进"]
    assert carry_followup_topic("怎么登记", history) == "怎么登记"
    assert carry_followup_topic("那晚上呢", history) == "那晚上呢"
    assert carry_followup_topic("那要钱吗", ["开车停哪里"]).endswith("：那要钱吗")
