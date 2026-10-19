"""真实 ConversationService 链路上的轮次计划：任务写入口、设施、软判定复核与危险补漏。

断言实际任务写入、员工通知、会话模式与客人最终正文，不只断言布尔结论（Spec §2.7）。
"""

from dataclasses import replace

import pytest

from homestay_bot.application import _deferred_message_from_payload
from homestay_bot.domain.enums import ConversationMode, Language
from homestay_bot.integrations.deepseek_client import AssistantDecision, FacilityIssue
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.turn_plan import PlanOutcome
from tests.plan_helpers import plan_for
from tests.unit.test_conversation_service import (
    AssistantStub,
    BusinessTaskStub,
    CustomerProfileStub,
    DeferredJobStub,
    FailingAssistantStub,
    MessageServiceStub,
    build_service,
    incoming,
)


def _facility_decision() -> AssistantDecision:
    """模型把整句判为民宿设施问题时的决定。"""
    return AssistantDecision(
        reply_text="好的。",
        language=Language.ZH,
        intent="facility_issue",
        confidence=0.9,
        facility_issue=FacilityIssue(scope="homestay_facility"),
        facility_advice=["请先停止使用空调"],
    )


async def _guest_turn(text: str, assistant: AssistantStub):
    """即时入口处理一条客人消息，返回任务、客人正文与员工通知。"""
    tasks = BusinessTaskStub()
    service, conversations, _, wecom = build_service(
        assistant=assistant, customer_profiles=CustomerProfileStub(), business_tasks=tasks
    )
    await service.handle_message(incoming(content=text))
    return tasks, wecom, conversations


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["上次房间空调坏了，现在已经修好了", "房间空调没有故障，不用维修"])
async def test_planned_history_or_negation_creates_no_facility_task(text: str) -> None:
    """计划判为历史或否定：即使模型与词面都指向设施，也不建任务、不出现「已提交」。"""
    tasks, wecom, _ = await _guest_turn(
        text, AssistantStub(decision=_facility_decision(), plan="history_mention")
    )
    assert tasks.calls == []
    assert "已提交" not in wecom.guest_messages[-1]
    assert "已登记" not in wecom.guest_messages[-1]


@pytest.mark.asyncio
async def test_main_reply_failure_with_a_history_plan_creates_no_task() -> None:
    """规划成功、主回复失败（V6-R1）：已修好不建任务、不回「已提交」。"""
    tasks, wecom, _ = await _guest_turn(
        "上次房间空调坏了，现在已经修好了", FailingAssistantStub(plan="history_mention")
    )
    assert tasks.calls == []
    assert all("已提交" not in message for message in wecom.guest_messages)


@pytest.mark.asyncio
async def test_main_reply_failure_with_a_current_fault_plan_registers() -> None:
    """规划判为当前故障、主回复失败：照常登记，收尾与实际登记结果一致。"""
    tasks, wecom, _ = await _guest_turn("灯不亮了", FailingAssistantStub(plan="facility_fault"))
    assert len(tasks.calls) == 1
    assert "已提交管家人工处理" in wecom.guest_messages[-1]


@pytest.mark.asyncio
async def test_unplanned_fault_gives_safety_advice_and_asks_to_confirm() -> None:
    """规划失败或没有计划（D14）：有故障信号时给安全提示，请客人确认后再登记，不发「已提交」。"""
    tasks, wecom, _ = await _guest_turn("灯不亮了", FailingAssistantStub())
    assert tasks.calls == []
    reply = wecom.guest_messages[-1]
    assert "已提交" not in reply
    assert "请回复确认" in reply


@pytest.mark.asyncio
async def test_unplanned_service_request_only_asks_to_confirm() -> None:
    """规划失败而词面命中服务申请（D13）：不建任务、不通知管家，回固定确认话术。"""
    tasks, wecom, _ = await _guest_turn("请补两瓶矿泉水", AssistantStub())
    assert tasks.calls == []
    assert "确认后我马上登记" in wecom.guest_messages[-1]
    assert wecom.internal_messages == []


@pytest.mark.asyncio
async def test_withdrawn_booking_notifies_staff_without_a_task() -> None:
    """「不用帮我订房了」：撤回对象不在本计划，不建任务，通知管家（D11）。"""
    text = "不用帮我订房了"
    tasks, wecom, _ = await _guest_turn(
        text,
        AssistantStub(plan=lambda t: plan_for(t, ("request_withdraw", t, {"subject": "订房"}))),
    )
    assert tasks.calls == []
    assert any("客人撤回申请：订房" in message for message in wecom.internal_messages)


@pytest.mark.asyncio
async def test_request_then_withdraw_registers_nothing() -> None:
    """申请后同事项撤回：不登记、不发登记收尾。"""
    text = "请送两条毛巾，算了，毛巾不用送了"
    tasks, wecom, _ = await _guest_turn(
        text,
        AssistantStub(
            plan=lambda t: plan_for(
                t,
                ("service_request", "请送两条毛巾", {"subject": "毛巾"}),
                ("request_withdraw", "毛巾不用送了", {"subject": "毛巾", "withdraws": 1}),
            )
        ),
    )
    assert tasks.calls == []
    assert "已登记" not in wecom.guest_messages[-1]


@pytest.mark.asyncio
async def test_withdraw_then_other_request_registers_only_the_new_item() -> None:
    """「不用送毛巾了，麻烦送两瓶水」：只登记水，登记收尾与实际结果一致，且不转人工（D7）。"""
    text = "不用送毛巾了，麻烦送两瓶水"
    tasks, wecom, conversations = await _guest_turn(
        text,
        AssistantStub(
            plan=lambda t: plan_for(
                t,
                ("request_withdraw", "不用送毛巾了", {"subject": "毛巾"}),
                ("service_request", "麻烦送两瓶水", {"subject": "水"}),
            )
        ),
    )
    assert len(tasks.calls) == 1
    assert "您的请求已登记" in wecom.guest_messages[-1]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


def _deferred(text: str, assistant: AssistantStub, *, commit_log: list[str] | None = None):
    """合并入口的会话服务：带作业与提交边界，计划可经载荷传到后台。"""
    jobs = DeferredJobStub()
    messages = MessageServiceStub()
    source = incoming(content=text)
    messages.recorded.append(source)
    log = commit_log if commit_log is not None else []

    async def commit() -> None:
        log.append("commit")

    service, conversations, _, wecom = build_service(
        assistant=assistant, jobs=jobs, messages=messages, complaint_service=ComplaintService(),
        commit_boundary=commit, customer_profiles=CustomerProfileStub(),
    )
    return service, conversations, wecom, jobs, source, messages


@pytest.mark.asyncio
async def test_happy_exclamation_with_a_calm_plan_continues_normally() -> None:
    """「第一次来太开心了!!!」计划 risk=none：不进客诉、不通知员工（D9），计划随最终任务传递。"""
    text = "第一次来太开心了!!!"
    service, conversations, wecom, jobs, source, _ = _deferred(
        text, AssistantStub(plan="chitchat")
    )
    await service.process_debounced_message(source)
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
    assert wecom.internal_messages == []
    _, payload, dedupe_key, _ = jobs.jobs[0]
    assert dedupe_key == f"final:{source.msgid}"
    restored = _deferred_message_from_payload(payload)
    plan = PlanOutcome.from_payload(restored.metadata["turn_plan"])
    assert plan is not None and plan.matches(text)

    await service.process_recorded_message(restored)
    assert service._assistant.last_kwargs["turn_plan"] == plan
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
    assert "我已收到您的诉求" not in wecom.guest_messages[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan", [lambda t: plan_for(t, ("chitchat", t, {"risk": "complaint"})), None]
)
async def test_complaint_plan_or_planning_failure_keeps_the_complaint_path(plan) -> None:
    """计划标为投诉，或规划失败：沿用现行规则进入客诉模式。"""
    service, conversations, wecom, jobs, source, _ = _deferred(
        "第一次来太开心了!!!", AssistantStub(plan=plan)
    )
    await service.process_debounced_message(source)
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert wecom.guest_messages == [ComplaintService.guest_acknowledgement()]
    assert jobs.jobs == []


@pytest.mark.asyncio
async def test_immediate_entry_defers_a_soft_complaint_to_the_debounce_job() -> None:
    """即时入口命中情绪词时不在请求里规划：登记合并作业后返回（V5-R2）。"""
    jobs = DeferredJobStub()
    assistant = AssistantStub(plan="chitchat")
    service, conversations, _, wecom = build_service(
        assistant=assistant, jobs=jobs, complaint_service=ComplaintService()
    )
    await service.handle_message(incoming(content="第一次来太开心了!!!"))
    assert assistant.plan_calls == 0
    assert [item[2] for item in jobs.jobs] == ["debounce:msg-1"]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
    assert wecom.guest_messages == []


@pytest.mark.asyncio
async def test_planning_releases_the_lock_and_discards_stale_results() -> None:
    """规划前先提交释放活动锁；规划期间出现新活动时丢弃结果，不进客诉、不排最终任务。"""
    log: list[str] = []
    text = "第一次来太开心了!!!"

    class _Planner(AssistantStub):
        async def plan_turn(self, *, text, language):
            log.append("plan")
            messages.recorded.append(incoming(content="又来一条", msgid="msg-2"))
            return await super().plan_turn(text=text, language=language)

    service, conversations, wecom, jobs, source, messages = _deferred(
        text, _Planner(plan="chitchat"), commit_log=log
    )
    await service.process_debounced_message(source)
    assert log[:2] == ["commit", "plan"]
    assert conversations.locked_ids.count(1) >= 2
    assert jobs.jobs == []
    assert wecom.guest_messages == []
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_unrelated_wording_with_a_property_question_continues() -> None:
    """「Is the fridge stocked with water?」无关词表误中：计划有本店事实项时继续正常流程。"""
    service, _, wecom, jobs, source, _ = _deferred(
        "Is the fridge stocked with water?", AssistantStub(plan="static_fact")
    )
    await service.process_debounced_message(source)
    assert len(jobs.jobs) == 1
    assert wecom.guest_messages == []


@pytest.mark.asyncio
async def test_unrelated_reply_stands_when_planning_fails() -> None:
    """规划失败时无关判定沿用现行规则。"""
    service, _, wecom, jobs, source, _ = _deferred(
        "Can you recommend some stocks to buy?", AssistantStub()
    )
    await service.process_debounced_message(source)
    assert jobs.jobs == []
    assert len(wecom.guest_messages) == 1


@pytest.mark.asyncio
async def test_human_active_calm_exclamation_is_answered_in_the_final_job() -> None:
    """HUMAN_ACTIVE 下只命中情绪词：后台释放锁规划，计划判为非客诉时继续主回复。"""
    log: list[str] = []
    service, conversations, wecom, _, source, _ = _deferred(
        "第一次来太开心了!!!", AssistantStub(plan="chitchat"), commit_log=log
    )
    conversations.conversation.mode = ConversationMode.HUMAN_ACTIVE
    await service.process_recorded_message(source)
    assert service._assistant.calls == 1
    assert log == ["commit", "commit"]
    assert wecom.guest_messages


@pytest.mark.asyncio
async def test_planned_current_hazard_escalates_when_wording_misses() -> None:
    """计划判为当前危险而词面未命中：升级完整处置并接管，只升不降（Spec §2.6）。"""
    text = "插座那边窜白烟"
    tasks, wecom, conversations = await _guest_turn(
        text, AssistantStub(plan=lambda t: plan_for(t, ("facility_fault", t,
                                                          {"risk": "current_hazard:fire"})))
    )
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert tasks.calls == []
    assert "119" in wecom.guest_messages[-1]


def test_payload_without_a_plan_restores_no_plan() -> None:
    """旧载荷或未经软判定的载荷不带计划，后台按无计划重新规划。"""
    message = _deferred_message_from_payload(
        {
            "msgid": "m", "open_kfid": "k", "external_userid": "u", "origin": "guest",
            "msgtype": "text", "content": "早餐", "sent_at": "2026-10-07T00:00:00+00:00",
        }
    )
    assert "turn_plan" not in (message.metadata or {})
    assert replace(message).content == "早餐"


@pytest.mark.asyncio
async def test_current_fault_always_carries_a_stop_using_tip() -> None:
    """当前故障的安全提示与建任务分开、由本地保证（Spec §2.5）：模型建议没写停用时补上，
    不挤掉模型建议；已写停用时不重复（候选门禁 F-跳闸 实测建议措辞不稳定）。"""
    decision = AssistantDecision(
        reply_text="好的。", language=Language.ZH, intent="facility_issue", confidence=0.9,
        facility_issue=FacilityIssue(scope="homestay_facility"),
        facility_advice=["把跳下的电闸开关推回一次"],
    )
    _, wecom, _ = await _guest_turn(
        "吹风机一开就跳闸了", AssistantStub(decision=decision, plan="facility_fault")
    )
    reply = wecom.guest_messages[-1]
    assert "把跳下的电闸开关推回一次" in reply
    assert "请先停止使用" in reply

    stop = decision.model_copy(update={"facility_advice": ["先停止使用吹风机"]})
    _, wecom, _ = await _guest_turn(
        "吹风机一开就跳闸了", AssistantStub(decision=stop, plan="facility_fault")
    )
    assert wecom.guest_messages[-1].count("停止使用") == 1


def _fire_plan(text: str) -> PlanOutcome:
    """词面未命中、计划判为当前火情的计划。"""
    return plan_for(text, ("facility_fault", text, {"risk": "current_hazard:fire"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("stub", [AssistantStub, FailingAssistantStub])
async def test_planned_hazard_is_handled_whether_main_reply_succeeds_or_fails(stub) -> None:
    """B24-R1：同一当前危险计划，主回复成功或失败都发完整紧急处置、接管并按紧急事件通知。"""
    tasks, wecom, conversations = await _guest_turn("插座那边窜白烟", stub(plan=_fire_plan))
    assert "119" in wecom.guest_messages[-1]
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert any("紧急事件" in message for message in wecom.internal_messages)
    assert not any("模型服务暂时不可用" in message for message in wecom.internal_messages)
    assert tasks.calls == []


@pytest.mark.asyncio
async def test_failed_reply_with_a_stale_plan_does_not_escalate() -> None:
    """B24-R1 对照：异常带回的计划与本轮正文摘要不一致时按无计划处理，不升级紧急。"""
    stale = _fire_plan("另一句话")
    _, wecom, _ = await _guest_turn("插座那边窜白烟", FailingAssistantStub(plan=lambda _t: stale))
    assert "119" not in wecom.guest_messages[-1]


@pytest.mark.asyncio
async def test_failed_reply_with_possible_hazard_plan_warns_first() -> None:
    """B24-R1：主回复失败时，计划的可能危险同样先给避险提醒。"""
    text = "房间里有点怪味"
    plan = lambda t: plan_for(t, ("facility_fault", t, {"risk": "possible_hazard"}))  # noqa: E731
    _, wecom, _ = await _guest_turn(text, FailingAssistantStub(plan=plan))
    assert any("避开可能有危险" in message for message in wecom.guest_messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ConversationMode.BOT_ACTIVE, ConversationMode.HUMAN_ACTIVE])
async def test_split_refund_after_happy_exclamation_keeps_the_refund_route(mode) -> None:
    """B24-R2：「第一次来太开心了!!! 退\\n款」不受平静计划影响，按退款进客诉或高风险护栏。"""
    text = "第一次来太开心了!!! 退\n款"
    service, conversations, wecom, jobs, source, _ = _deferred(
        text, AssistantStub(plan=lambda t: plan_for(t, ("chitchat", t)))
    )
    conversations.conversation.mode = mode
    await service.process_debounced_message(source)
    assert jobs.jobs == []
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert wecom.internal_messages
    assert service._assistant.plan_calls == 0


@pytest.mark.asyncio
async def test_immediate_entry_routes_split_refund_without_deferring() -> None:
    """B24-R2：即时入口同样先看全部文本变体里的硬理由，不把退款当成软判定延后。"""
    jobs = DeferredJobStub()
    service, conversations, _, wecom = build_service(
        assistant=AssistantStub(plan="chitchat"), jobs=jobs, complaint_service=ComplaintService()
    )
    await service.handle_message(incoming(content="第一次来太开心了!!! 退\n款"))
    assert jobs.jobs == []
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
    assert wecom.internal_messages


@pytest.mark.asyncio
async def test_service_reply_is_the_action_result_when_model_text_is_all_promise() -> None:
    """SR-毛巾：模型正文全是执行承诺被出口过滤时，客人只收到实际登记结果，不出现
    「暂时无法确认」或「尚未确认」。"""
    decision = AssistantDecision(
        reply_text="马上为您安排送两条毛巾。", language=Language.ZH, intent="service",
        confidence=0.9,
    )
    tasks, wecom, _ = await _guest_turn(
        "毛巾不够用了，能再给两条吗", AssistantStub(decision=decision, plan="service_request")
    )
    reply = wecom.guest_messages[-1]
    assert len(tasks.calls) == 1
    assert reply.startswith("您的请求已登记")
    assert "暂时无法确认" not in reply
    assert "尚未确认" not in reply


# ── 1.70.6 危险优先于提前返回的确认（规划误判 Spec §8，H176-R1、R2）────────────────

POSSIBLE_TEXT = "请先避开可能有危险的位置。现在具体是什么情况？"
ELECTRIC_MARKER = "不要触碰"
COMPLAINT_ACK = "您的情况我已记录"
# 人工高风险确认「我已收到您的诉求」经现有高危策略统一为客诉确认同一句（C8-2 已说明）。
HUMAN_ACK = COMPLAINT_ACK


def _split_plan(fault_quote: str, fault_risk: str, other_quote: str, other: tuple[str, str]):
    """故障项与另一项（投诉或议价）同句拆分的已核验计划工厂。"""
    kind, risk = other

    def make(text: str) -> PlanOutcome:
        return plan_for(text, ("facility_fault", fault_quote, {"risk": fault_risk}),
                        (kind, other_quote, {"risk": risk}))
    return make


async def _run_soft(text: str, make_plan, *, mode=ConversationMode.BOT_ACTIVE):
    """经真实即时入口与合并入口跑一轮软复核；返回服务各端口与助手。"""
    assistant = AssistantStub(plan=make_plan)
    service, conversations, wecom, jobs, source, messages = _deferred(text, assistant)
    conversations.conversation.mode = mode
    await service.process_debounced_message(source)
    return assistant, conversations, wecom, jobs, source


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_risk,expect_possible,expect_electric", [
    ("possible_hazard", True, False),
    ("current_hazard:electric", False, True),
    ("none", False, False),
])
async def test_soft_complaint_consumes_plan_hazard_first(fault_risk, expect_possible,
                                                         expect_electric):
    """「插座一直滋滋响还发烫！！！太离谱了」故障项带危险、另一项为投诉：
    - 可能危险：一次组合回复（避险正文在前、客诉确认在后），两条员工通知；
    - 当前危险：电气危险处置并接管，不再发客诉确认；
    - 无危险：只进客诉（原有行为）。三种都不进入主回复。"""
    text = "插座一直滋滋响还发烫！！！太离谱了"
    make = _split_plan("插座一直滋滋响还发烫", fault_risk, "太离谱了", ("unclear", "complaint"))
    assistant, conversations, wecom, jobs, source = await _run_soft(text, make)
    assert assistant.plan_calls == 1 and assistant.calls == 0
    assert not [job for job in jobs.jobs if job[2] == f"final:{source.msgid}"]
    assert len(wecom.guest_messages) == 1
    reply = wecom.guest_messages[0]
    notes = " ".join(wecom.internal_messages)
    if expect_electric:
        # 紧急处置的固定回复本身含「已记录」，是否进入客诉看员工通知。
        assert ELECTRIC_MARKER in reply and "客诉待处理" not in notes
    else:
        assert COMPLAINT_ACK in reply
        assert (POSSIBLE_TEXT in reply) is expect_possible
        if expect_possible:
            assert reply.index(POSSIBLE_TEXT) < reply.index(COMPLAINT_ACK)
        assert ("可能的安全情况" in notes) is expect_possible and "客诉待处理" in notes
    assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_risk,expect_possible,expect_electric", [
    ("possible_hazard", True, False),
    ("current_hazard:electric", False, True),
    ("none", False, False),
])
async def test_human_high_risk_ack_consumes_plan_hazard_first(fault_risk, expect_possible,
                                                              expect_electric):
    """人工接待中「插座一直滋滋响还发烫！！！可以便宜点吗」：没有投诉但有硬理由 price，原来直接
    回通用确认；现在先消费计划危险（H176-R2）。三种都不进入主回复。"""
    text = "插座一直滋滋响还发烫！！！可以便宜点吗"
    make = _split_plan("插座一直滋滋响还发烫", fault_risk, "可以便宜点吗", ("static_fact", "none"))
    assistant, _, wecom, jobs, source = await _run_soft(
        text, make, mode=ConversationMode.HUMAN_ACTIVE
    )
    assert assistant.plan_calls == 1 and assistant.calls == 0
    assert not [job for job in jobs.jobs if job[2] == f"final:{source.msgid}"]
    assert len(wecom.guest_messages) == 1
    reply = wecom.guest_messages[0]
    notes = " ".join(wecom.internal_messages)
    if expect_electric:
        assert ELECTRIC_MARKER in reply and "人工接待会话收到新的高风险消息" not in notes
    else:
        assert HUMAN_ACK in reply and (POSSIBLE_TEXT in reply) is expect_possible
        if expect_possible:
            assert reply.index(POSSIBLE_TEXT) < reply.index(HUMAN_ACK)
        assert ("可能的安全情况" in notes) is expect_possible
        assert "人工接待会话收到新的高风险消息" in notes


@pytest.mark.asyncio
async def test_soft_complaint_possible_hazard_reply_in_english():
    """英文会话的组合回复：英文避险正文（含追问）在前，客诉确认在后，仍是一条。"""
    text = "The socket keeps buzzing and is hot!!! This is ridiculous"
    make = _split_plan("The socket keeps buzzing and is hot", "possible_hazard",
                       "This is ridiculous", ("unclear", "complaint"))
    assistant = AssistantStub(plan=make)
    service, conversations, wecom, _, source, _ = _deferred(text, assistant)
    conversations.conversation.language = Language.EN
    await service.process_debounced_message(source)
    assert len(wecom.guest_messages) == 1
    assert wecom.guest_messages[0].startswith(
        "Please avoid the suspected hazard for now. What is happening at the moment?"
    )


@pytest.mark.asyncio
async def test_plan_hazard_is_not_consumed_when_plan_does_not_match_text():
    """计划对不上本轮正文（来源摘要不一致）：不消费危险，按既有规则进入客诉。"""
    text = "插座一直滋滋响还发烫！！！太离谱了"
    other = "别的一句话！！！"

    def make(_: str) -> PlanOutcome:
        return plan_for(other, ("facility_fault", "别的一句话",
                                {"risk": "current_hazard:electric"}))

    _, _, wecom, _, _ = await _run_soft(text, make)
    assert len(wecom.guest_messages) == 1
    assert COMPLAINT_ACK in wecom.guest_messages[0]
    assert ELECTRIC_MARKER not in wecom.guest_messages[0]


def test_plan_hazard_keeps_both_dedupe_conditions():
    """C8-1：当前危险只在词面已判紧急时不处置——词面只判可能危险、计划为当前危险时仍升级；
    可能危险在词面已判可能危险或紧急时不重复。"""
    from homestay_bot.services.conversation_service import ConversationService
    from homestay_bot.services.emergency_service import EmergencyService

    emergency = EmergencyService()
    lexical_possible = "插座好像漏电了！！！太离谱了"
    assert emergency.classify(lexical_possible).is_possible
    current = plan_for(lexical_possible, ("facility_fault", "插座好像漏电了",
                                          {"risk": "current_hazard:electric"}))
    possible = plan_for(lexical_possible, ("facility_fault", "插座好像漏电了",
                                           {"risk": "possible_hazard"}))
    assert ConversationService._plan_hazard(lexical_possible, current, emergency) == (
        "current", "electric"
    )
    assert ConversationService._plan_hazard(lexical_possible, possible, emergency) is None
    quiet = "插座一直滋滋响还发烫"
    assert ConversationService._plan_hazard(quiet, plan_for(quiet, (
        "facility_fault", quiet, {"risk": "possible_hazard"})), emergency) == ("possible", None)
