"""fix3 的真实事务与最终出站边界；仅使用临时 SQLite 和合成模型响应。"""

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import ConversationMode, KnowledgeReferralStatus
from homestay_bot.domain.models import Base, Conversation, KnowledgeEntry, SourceCompletionReceipt
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.repositories.knowledge_referrals import SQLAlchemyKnowledgeReferralRepository
from homestay_bot.routes.knowledge import KnowledgeAdminService, KnowledgeFormError
from homestay_bot.services.handoff_card import HANDOFF_CARD_JOB_TYPE
from homestay_bot.services.knowledge_service import KnowledgeService
from tests.integration import test_knowledge_referrals as referrals
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _availability_row,
    _decision,
    _Knowledge,
    _snippet,
    _tool_message,
    _Tools,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("day", ["明天", "第二天", "第2天", "次日", "the next day"])
async def test_unbound_relative_day_has_no_wrong_fact_or_referral(factory, day, fallback):
    """相对日期无法落在项范围时，真实最终出站不得混入首日事实或创建转交卡片。"""
    text = f"10月10日入住10月12日退房有房吗，{day}早餐几点开始"
    args = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"}
    plan = plan_for(text, ("stay_query", text, args))
    decisions, offered = [], []
    async with factory() as session:
        session.add(KnowledgeEntry(
            id=6001, category="早餐", scope="global", is_enabled=True,
            question_zh="早餐几点开始？", answer_zh="早餐7:30开始。",
            question_en="When is breakfast?", answer_en="Breakfast starts at 7:30.",
            keywords=["早餐"], valid_from=date(2026, 10, 10),
            valid_until=date(2026, 10, 10),
        ))
        await session.commit()
        assistant, completions = _assistant([], KnowledgeService(
            SQLAlchemyKnowledgeRepository(session)
        ), _Tools({"search_availability": [_availability_row(1401, *args.values())]}))

        async def choose(**kwargs):
            """模型选择助手实际提供的所有早餐缺口，不能靠模型恰好未选隐藏误转交。"""
            completions.requests.append(kwargs)
            if len(completions.requests) == 1:
                envelope = json.loads(kwargs["messages"][-1]["content"])
                offered.extend(u for u in envelope.get("gap_units", []) if "早餐" in u["text"])
                message = _tool_message("search_availability", args)
            else:
                message = SimpleNamespace(
                    content="invalid" if fallback else _decision(gap_referrals=[{
                        "unit_id": unit["id"], "canonical_question": "早餐几点开始",
                        "category": "早餐",
                    } for unit in offered]), tool_calls=None,
                )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        completions.create = choose

        async def respond(**kwargs):
            """注入生产核验的粗计划；助手与真实会话、转交和发件箱仍执行完整链路。"""
            kwargs["turn_plan"] = plan
            decision = await assistant.respond(**kwargs)
            decisions.append(decision)
            return decision

        _, message = await referrals._seed(factory, content=text)
        await referrals._run_final(factory, SimpleNamespace(respond=respond), message)
    [reply] = await referrals._jobs(factory, "wecom_send_text")
    assert "7:30" not in reply.payload["content"]
    assert "401" in reply.payload["content"]
    assert "已提交管家核实" not in reply.payload["content"]
    assert offered == []
    assert [(g.text, g.reason) for g in decisions[0].gap_units] == [
        (f"{day}早餐几点开始", "scope_unbound")
    ]
    assert not await referrals._referrals(factory)
    assert not await referrals._jobs(factory, HANDOFF_CARD_JOB_TYPE)


@pytest.mark.asyncio
@pytest.mark.parametrize("planned_day", [None, 10, 9])
@pytest.mark.parametrize("fallback", [False, True])
async def test_independent_weekday_uses_real_dated_knowledge(factory, planned_day, fallback):
    """真实检索和最终发件箱只保留独立周六资料；不借当前住宿日，冲突计划零答案零转交。"""
    question = "这周六早餐几点开始"
    fields = {} if planned_day is None else {
        "check_in_date": f"2026-10-{planned_day:02d}",
        "check_out_date": f"2026-10-{planned_day + 1:02d}",
    }
    items = [("static_fact", question, fields)]
    text = question
    if fallback:
        first = "10月7日入住10月9日退房有房吗"
        args = {"check_in_date": "2026-10-07", "check_out_date": "2026-10-09"}
        items.insert(0, ("stay_query", first, args))
        text = first + "，" + question
    decisions = []
    async with factory() as session:
        for day, time in [(7, "7:30"), (9, "9:30"), (10, "8:30")]:
            session.add(KnowledgeEntry(
                id=6100 + day, category="早餐", scope="global", is_enabled=True,
                question_zh="早餐几点开始？", answer_zh=f"早餐{time}开始。",
                question_en="When is breakfast?", answer_en=f"Breakfast starts at {time}.",
                keywords=["早餐"], valid_from=date(2026, 10, day),
                valid_until=date(2026, 10, day),
            ))
        await session.commit()
        responses = [_tool_message("search_availability", args), "invalid"] if fallback else [
            _decision(),
        ]
        tools = _Tools({"search_availability": [
            _availability_row(1401, *args.values()),
        ]}) if fallback else None
        assistant, _ = _assistant(responses, KnowledgeService(
            SQLAlchemyKnowledgeRepository(session)
        ), tools)
        plan = plan_for(text, *items)

        async def respond(**kwargs):
            """保持真实助手和会话事务，只注入已核验计划与合成模型。"""
            kwargs["turn_plan"] = plan
            decision = await assistant.respond(**kwargs)
            decisions.append(decision)
            return decision

        _, message = await referrals._seed(factory, content=text)
        await referrals._run_final(factory, SimpleNamespace(respond=respond), message)
    [reply] = await referrals._jobs(factory, "wecom_send_text")
    assert "7:30" not in reply.payload["content"]
    assert "9:30" not in reply.payload["content"]
    assert ("8:30" in reply.payload["content"]) is (planned_day != 9)
    assert [g.reason for g in decisions[0].gap_units] == (
        ["scope_unbound"] if planned_day == 9 else []
    )
    assert not await referrals._referrals(factory)
    assert not await referrals._jobs(factory, HANDOFF_CARD_JOB_TYPE)


@pytest.mark.asyncio
@pytest.mark.parametrize("fine_plan,missing", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("fallback", [False, True])
async def test_calendar_day_inside_scope_keeps_only_that_day(factory, fine_plan, missing, fallback):
    """确定的明天在已有范围内时只核当天；粗项、细分项与回退均保留资料或合法缺口。"""
    first, second = "10月7日入住10月10日退房有房吗", "明天早餐几点开始"
    text = first + "，" + second
    args = {"check_in_date": "2026-10-07", "check_out_date": "2026-10-10"}
    plan = plan_for(text, ("stay_query", first, args), ("static_fact", second, {
        "check_in_date": "2026-10-08", "check_out_date": "2026-10-09",
    })) if fine_plan else plan_for(text, ("stay_query", text, args))
    decisions = []
    async with factory() as session:
        for day, time in [(7, "7:30"), *([] if missing else [(8, "8:30")])]:
            session.add(KnowledgeEntry(
                id=6000 + day, category="早餐", scope="global", is_enabled=True,
                question_zh="早餐几点开始？", answer_zh=f"早餐{time}开始。",
                question_en="When is breakfast?", answer_en=f"Breakfast starts at {time}.",
                keywords=["早餐"], valid_from=date(2026, 10, day),
                valid_until=date(2026, 10, day),
            ))
        await session.commit()
        assistant, completions = _assistant([], KnowledgeService(
            SQLAlchemyKnowledgeRepository(session)
        ), _Tools({"search_availability": [_availability_row(1401, *args.values())]}))
        offered = []

        async def choose(**kwargs):
            """选择信封里真正开放的早餐缺口，资料完整时本地核验必须拒绝转交。"""
            completions.requests.append(kwargs)
            if len(completions.requests) == 1:
                envelope = json.loads(kwargs["messages"][-1]["content"])
                offered.extend(u for u in envelope.get("gap_units", []) if "早餐" in u["text"])
                message = _tool_message("search_availability", args)
            else:
                message = SimpleNamespace(
                    content="invalid" if fallback else _decision(gap_referrals=[{
                        "unit_id": unit["id"], "canonical_question": "早餐几点开始",
                        "category": "早餐",
                    } for unit in offered]), tool_calls=None,
                )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        completions.create = choose

        async def respond(**kwargs):
            """固定已核验计划而保留真实助手到事务发件箱的行为。"""
            kwargs["turn_plan"] = plan
            decision = await assistant.respond(**kwargs)
            decisions.append(decision)
            return decision

        _, message = await referrals._seed(factory, content=text)
        await referrals._run_final(factory, SimpleNamespace(respond=respond), message)
    [reply] = await referrals._jobs(factory, "wecom_send_text")
    body = reply.payload["content"]
    assert "7:30" not in body
    assert ("8:30" in body) is (not missing)
    expected = int(missing and not fallback)
    assert len(await referrals._referrals(factory)) == expected
    assert len(await referrals._jobs(factory, HANDOFF_CARD_JOB_TYPE)) == expected
    assert ("已提交管家核实" in body) is bool(expected)
    if missing:
        assert [(g.text, g.reason) for g in decisions[0].gap_units] == [
            (second, "knowledge_missing")
        ]
    else:
        dates = [e.target_date for part in decisions[0].reply_parts for e in part.evidence
                 if e.source_kind == "knowledge"]
        assert dates == [date(2026, 10, 8)]


@pytest_asyncio.fixture
async def factory(tmp_path):
    """项目真实引擎的文件库允许多个会话确定性交错，退出时释放全部连接。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'fix3.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("case", ["date_inside", "date_outside", "price_iso"])
async def test_static_scope_and_residue_survive_final_outbox(factory, fallback, case):
    """真实会话最终出站保留日期隔离与早餐答案；正常/协议回退均不误接管或转交。"""
    price = case == "price_iso"
    checkout = "2026-10-11" if price else "2026-10-12"
    text = "2026-10-10 401房间住一晚多少钱和早餐几点开始" if price else (
        f"10月10日401住2晚有房吗，10月{11 if case == 'date_inside' else 13}日早餐几点开始"
    )
    plan = plan_for(text, ("stay_query", text, {
        "target_room": "401", "check_in_date": "2026-10-10", "check_out_date": checkout,
    }))
    args = {"check_in_date": "2026-10-10", "check_out_date": checkout}
    knowledge = _Knowledge([_snippet(
        9702, "早餐几点开始？", "早餐8:30开始。" if price else "早餐7:30开始。",
        category="早餐", valid_from=date(2026, 10, 10), valid_until=date(2026, 10, 10),
    )], {"401": 1401})
    assistant, completions = _assistant(
        [_tool_message("search_availability", args), "invalid" if fallback else _decision()],
        knowledge, _Tools({"search_availability": [_availability_row(1401, *args.values())]}),
    )
    decisions = []

    async def respond(**kwargs):
        """仅注入已核验计划，实际助手、会话、仓储和发件箱照常执行。"""
        kwargs["turn_plan"] = plan
        decision = await assistant.respond(**kwargs)
        decisions.append(decision)
        return decision

    conversation_id, message = await referrals._seed(factory, content=text)
    await referrals._run_final(factory, SimpleNamespace(respond=respond), message)
    [reply] = await referrals._jobs(factory, "wecom_send_text")
    body = reply.payload["content"]
    assert len(completions.requests) == 2
    assert "7:30" not in body
    assert ("8:30" in body) is price
    assert "已提交管家核实" not in body
    expected_reason = "knowledge_missing" if case == "date_inside" else "scope_unbound"
    if price:
        assert all(unit.reason != "scope_unbound" for unit in decisions[0].gap_units)
    else:
        assert any(unit.reason == expected_reason and "早餐" in unit.text
                   for unit in decisions[0].gap_units)
    assert not await referrals._referrals(factory)
    assert not await referrals._jobs(factory, HANDOFF_CARD_JOB_TYPE)
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.mode is ConversationMode.BOT_ACTIVE
        assert (await session.get(SourceCompletionReceipt, message.msgid)).referral_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize("known,unknown_id", [(False, False), (True, False), (False, True)])
async def test_offered_clause_id_reaches_final_outbox(factory, known, unknown_id):
    """从真实信封选第一句：缺资料只转一条，第二句越界不转交，已知或未知编号都零转交。"""
    first = "民宿前台10月10日早餐几点开始"
    text = first + "，民宿前台10月11日健身房几点开放"
    plan = plan_for(text, ("static_fact", text, {
        "check_in_date": "2026-10-10", "check_out_date": "2026-10-11",
    }))
    entries = [_snippet(
        9701, "早餐几点开始？", "早餐8:30开始。", category="早餐",
        valid_from=date(2026, 10, 10), valid_until=date(2026, 10, 10),
    )] if known else []
    assistant, completions = _assistant([_decision()], _Knowledge(entries))
    decisions = []

    async def choose(**kwargs):
        """只替换网络响应，编号取助手本次真正发出的信封。"""
        completions.requests.append(kwargs)
        envelope = json.loads(kwargs["messages"][-1]["content"])
        offered = envelope["gap_units"]
        assert offered == [{"id": f"1:0-{len(first)}", "text": first}]
        selected = "unknown" if unknown_id else offered[0]["id"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=_decision(gap_referrals=[{
                "unit_id": selected, "canonical_question": "早餐几点开始", "category": "早餐",
            }]), tool_calls=None,
        ))])

    completions.create = choose

    class PlannedAssistant:
        """服务请求进入实际助手，并注入经生产核验的固定计划，保留真实回复与转交流程。"""

        async def respond(self, **kwargs):
            """仅跳过规划模型的网络调用。"""
            kwargs["turn_plan"] = plan
            decision = await assistant.respond(**kwargs)
            decisions.append(decision)
            return decision

    _, message = await referrals._seed(factory, content=text)
    await referrals._run_final(factory, PlannedAssistant(), message)
    records = await referrals._referrals(factory)
    cards = await referrals._jobs(factory, HANDOFF_CARD_JOB_TYPE)
    [reply] = await referrals._jobs(factory, "wecom_send_text")
    expected = 0 if known or unknown_id else 1
    assert len(completions.requests) == 1
    assert len(decisions[0].referral_candidates) == expected, decisions[0]
    assert len(records) == len(cards) == expected
    assert "尚未确认" in reply.payload["content"]
    assert ("已提交管家核实" in reply.payload["content"]) is bool(expected)
    if expected:
        assert records[0].unit_id == f"1:0-{len(first)}"
    if known:
        assert "8:30" in reply.payload["content"]
    async with factory() as session:
        receipt = await session.get(SourceCompletionReceipt, message.msgid)
        assert receipt.referral_ids == [record.id for record in records]


@pytest.mark.asyncio
async def test_clear_committed_before_conversion_rejects_stale_page(factory):
    """清空在入库校验等待时先提交，旧页面不得复活记录或创建孤立知识。"""
    await referrals._drafted(factory)
    reached, release = asyncio.Event(), asyncio.Event()

    class PausedAdmin(KnowledgeAdminService):
        """在真实范围校验后暂停，重现 SELECT 与 UPDATE 之间的竞争窗口。"""

        async def _validate_scope(self, fields):
            """不替换校验，仅提供事件屏障。"""
            await super()._validate_scope(fields)
            reached.set()
            await release.wait()

    async def convert():
        """旧页面以原代次提交房间范围。"""
        async with factory() as session:
            await PausedAdmin(session).convert_referral(
                1, None, generation=1, reviewed=True,
                **referrals._convert_fields(scope="property", property_id=401),
            )

    task = asyncio.create_task(convert())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        async with factory() as session:
            await SQLAlchemyKnowledgeReferralRepository(session).clear_for_conversations([1])
            await session.commit()
        release.set()
        with pytest.raises(KnowledgeFormError, match="刷新"):
            await asyncio.wait_for(task, 5)
        [record] = await referrals._referrals(factory)
        assert record.status is KnowledgeReferralStatus.CLEARED and record.generation == 2
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(KnowledgeEntry)) == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_conversion_committed_before_clear_keeps_knowledge(factory):
    """入库先完成，清空只清转交正文，保留已审核知识及关联。"""
    await referrals._drafted(factory)
    async with factory() as session:
        entry = await KnowledgeAdminService(session).convert_referral(
            1, None, generation=1, reviewed=True, **referrals._convert_fields(),
        )
        entry_id = entry.id
    async with factory() as session:
        await SQLAlchemyKnowledgeReferralRepository(session).clear_for_conversations([1])
        await session.commit()
    [record] = await referrals._referrals(factory)
    assert record.status is KnowledgeReferralStatus.CLEARED
    assert record.knowledge_entry_id == entry_id
    async with factory() as session:
        assert (await session.get(KnowledgeEntry, entry_id)).is_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("replies", [(), ("门锁密码：1#2#3#4",), ("健身房七点开门",)])
async def test_draft_sources_determine_terminal_state_and_job_generation(factory, replies):
    """无可用来源直接终结；非空来源只入队一次，任务与记录使用同一个新代次。"""
    await referrals._referral_with_staff_replies(factory, *replies)
    async with factory() as session:
        await KnowledgeAdminService(session).start_referral_draft(1, None)
    [record] = await referrals._referrals(factory)
    jobs = await referrals._jobs(factory, "knowledge_referral_draft")
    usable = replies == ("健身房七点开门",)
    assert record.status is (
        KnowledgeReferralStatus.DRAFTING if usable else KnowledgeReferralStatus.NO_ANSWER
    )
    assert record.generation == 1 and len(jobs) == int(usable)
    if usable:
        assert jobs[0].payload["generation"] == record.generation
        async with factory() as session:
            with pytest.raises(KnowledgeFormError, match="刷新"):
                await KnowledgeAdminService(session).start_referral_draft(1, None)
        assert len(await referrals._jobs(factory, "knowledge_referral_draft")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action,winner", [
    ("start", "start"), ("start", "close"), ("start", "clear"), ("close", "clear"),
])
async def test_admin_cas_rejects_request_read_before_winner(factory, monkeypatch, action, winner):
    """实际请求读到旧状态后暂停，让另一入口先提交；旧请求必须被 CAS 拒绝。"""
    await referrals._referral_with_staff_replies(factory, "健身房七点开门")
    reached, release = asyncio.Event(), asyncio.Event()
    original = SQLAlchemyKnowledgeReferralRepository.get
    paused_session = None

    async def paused_get(repository, *args, **kwargs):
        """只暂停旧请求的真实查询，不改变返回内容或另一请求。"""
        result = await original(repository, *args, **kwargs)
        if repository._session is paused_session:
            reached.set()
            await release.wait()
        return result

    monkeypatch.setattr(SQLAlchemyKnowledgeReferralRepository, "get", paused_get)

    async def stale_request():
        """每次请求独立事务，复现 SQLite 没有 SELECT FOR UPDATE 的实际边界。"""
        nonlocal paused_session
        async with factory() as session:
            paused_session = session
            service = KnowledgeAdminService(session)
            method = service.start_referral_draft if action == "start" else service.close_referral
            await method(1, None)

    task = asyncio.create_task(stale_request())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        async with factory() as session:
            service = KnowledgeAdminService(session)
            if winner == "clear":
                await SQLAlchemyKnowledgeReferralRepository(session).clear_for_conversations([1])
                await session.commit()
            elif winner == "close":
                await service.close_referral(1, None)
            else:
                await service.start_referral_draft(1, None)
        release.set()
        with pytest.raises(KnowledgeFormError):
            await asyncio.wait_for(task, 5)
        [record] = await referrals._referrals(factory)
        expected = {"start": KnowledgeReferralStatus.DRAFTING,
                    "close": KnowledgeReferralStatus.CLOSED,
                    "clear": KnowledgeReferralStatus.CLEARED}
        assert record.status is expected[winner]
        jobs = await referrals._jobs(factory, "knowledge_referral_draft")
        assert len(jobs) == int(winner == "start")
        if winner != "start":
            async with factory() as session:
                assert not await SQLAlchemyKnowledgeReferralRepository(session).finish_draft(
                    1, generation=0, status=KnowledgeReferralStatus.DRAFTED, draft={"stale": True},
                )
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_enqueue_rolls_back_draft_claim(factory, monkeypatch):
    """CAS 成功后作业登记失败，状态、代次和冻结来源一起回滚。"""
    await referrals._referral_with_staff_replies(factory, "健身房七点开门")

    async def fail_enqueue(*args, **kwargs):
        """在真实事务取得资格后模拟队列写入失败。"""
        raise RuntimeError("synthetic enqueue failure")

    monkeypatch.setattr(SQLAlchemyJobRepository, "enqueue", fail_enqueue)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="synthetic"):
            await KnowledgeAdminService(session).start_referral_draft(1, None)
    [record] = await referrals._referrals(factory)
    assert record.status is KnowledgeReferralStatus.PENDING and record.generation == 0
    assert not record.source_message_ids and record.draft is None
    assert not await referrals._jobs(factory, "knowledge_referral_draft")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unlimited", "sunday_missing", "sunday_conflict"])
async def test_weekend_static_answer_and_referral_survive_final_outbox(factory, case):
    """真实检索到事务发件箱：周末按天取证；只有纯缺资料可转交，冲突段阻断整个原文单元。"""
    text = "这周末早餐几点开始"
    plan = plan_for(text, ("static_fact", text, {}))
    rows = {
        "unlimited": [(None, "7:30")],
        "sunday_missing": [(10, "8:30")],
        "sunday_conflict": [(10, "8:30"), (11, "9:30"), (11, "10:30")],
    }[case]
    async with factory() as session:
        for index, (day, time) in enumerate(rows):
            valid = {} if day is None else {
                "valid_from": date(2026, 10, day), "valid_until": date(2026, 10, day),
            }
            session.add(KnowledgeEntry(
                id=6100 + index, category="早餐", scope="global", is_enabled=True,
                question_zh="早餐几点开始？", answer_zh=f"早餐{time}开始。",
                question_en="When is breakfast?", answer_en=f"Breakfast starts at {time}.",
                keywords=["早餐"], **valid,
            ))
        await session.commit()
        assistant, completions = _assistant([], KnowledgeService(
            SQLAlchemyKnowledgeRepository(session)
        ))

        async def choose(**kwargs):
            """模型选中第一条资料，并申请转交信封里开放的早餐缺口；是否转交由本地核验决定。"""
            completions.requests.append(kwargs)
            envelope = json.loads(kwargs["messages"][-1]["content"])
            offered = [u for u in envelope.get("gap_units", []) if "早餐" in u["text"]]
            content = _decision(
                evidence_selection=[{"item_id": 1, "answer_ids": [6100]}],
                gap_referrals=[{
                    "unit_id": unit["id"], "canonical_question": "早餐几点开始",
                    "category": "早餐",
                } for unit in offered],
            )
            message = SimpleNamespace(content=content, tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        completions.create = choose

        async def respond(**kwargs):
            """固定已核验计划而保留真实助手到事务发件箱的行为。"""
            kwargs["turn_plan"] = plan
            return await assistant.respond(**kwargs)

        _, message = await referrals._seed(factory, content=text)
        await referrals._run_final(factory, SimpleNamespace(respond=respond), message)
    [reply] = await referrals._jobs(factory, "wecom_send_text")
    body = reply.payload["content"]
    expected = int(case == "sunday_missing")
    assert len(await referrals._referrals(factory)) == expected
    assert len(await referrals._jobs(factory, HANDOFF_CARD_JOB_TYPE)) == expected
    assert ("已提交管家核实" in body) is bool(expected)
    if case == "unlimited":
        assert "早餐7:30开始" in body
    else:
        assert "2026-10-10：早餐8:30开始。" in body
        assert "9:30" not in body and "10:30" not in body
