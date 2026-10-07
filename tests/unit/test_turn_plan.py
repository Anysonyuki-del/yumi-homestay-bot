"""轮次计划核验、统一任务写入口与统一接管理由（回复泛用化 Spec §2.3、§2.5、§2.6）。"""

import json

import pytest

from homestay_bot.domain.enums import BusinessTaskType
from homestay_bot.services.answer_policy import resolve_handoff_reason, resolve_task_request
from homestay_bot.services.turn_plan import failed_plan, verify_turn_plan
from tests.plan_helpers import PLAN_TODAY, plan_for


def _verify(text: str, items: list[dict]) -> object:
    """按原始 JSON 走生产核验。"""
    return verify_turn_plan(
        json.dumps({"items": items}, ensure_ascii=False), text, today_provider=lambda: PLAN_TODAY
    )


def _item(**fields) -> dict:
    """补齐计划项的必填字段。"""
    return {"id": 1, "kind": "static_fact", "quote": "", "start": 0, **fields}


def test_quote_must_come_from_the_guest_text() -> None:
    """摘录不在正文里时该项无效；全部无效即规划失败，不回退到词面授权。"""
    outcome = _verify("早餐几点", [_item(quote="停车多少钱")])
    assert outcome.status == "failed"
    assert outcome.reason == "all_quotes_invalid"


def test_a_unique_quote_corrects_a_wrong_start() -> None:
    """唯一出现的摘录本身确定位置：给错的 start 按唯一位置改正。"""
    outcome = _verify("早餐几点，停车多少钱", [_item(quote="停车多少钱", start=0)])
    assert outcome.ok
    assert outcome.plan.items[0].start == 5


def test_a_repeated_quote_with_a_wrong_start_is_invalid() -> None:
    """同一摘录出现多次又给错位置时无法判断指哪一处，该项无效。"""
    text = "请送毛巾，不用送毛巾了，还是请送毛巾"
    outcome = _verify(
        text,
        [
            _item(id=1, kind="service_request", quote="请送毛巾", start=3),
            _item(id=2, kind="request_withdraw", quote="不用送毛巾了", start=5),
        ],
    )
    assert outcome.ok
    assert outcome.plan.items[0].valid is False
    assert outcome.plan.items[1].valid is True


@pytest.mark.parametrize(
    "item",
    [
        _item(quote="早餐", kind="refund_now"),
        _item(quote="早餐", risk="angry"),
        _item(quote="早餐", risk="current_hazard:flood"),
        _item(quote="早餐", withdraws=1),
        _item(quote="早餐", id=0),
    ],
)
def test_schema_violations_fail_the_whole_plan(item: dict) -> None:
    """类型、风险、撤回字段不合规时整份计划失败，下游按现行保守规则处理。"""
    assert _verify("早餐几点", [item]).status == "failed"


def test_invalid_json_fails() -> None:
    """模型没返回 JSON 时规划失败。"""
    assert verify_turn_plan("不是JSON", "早餐", today_provider=lambda: PLAN_TODAY).reason == (
        "invalid_json"
    )


def test_target_room_and_dates_are_locally_checked() -> None:
    """房号必须出现在原文；日期走统一住宿日期校验，越界或缺一端都不保留。"""
    outcome = _verify(
        "401适合带小孩吗，下周三住两晚多少钱",
        [
            _item(id=1, quote="401适合带小孩吗", target_room="401"),
            _item(
                id=2, kind="stay_query", quote="下周三住两晚多少钱", target_room="302",
                check_in_date="2026-10-14", check_out_date="2026-10-16",
            ),
        ],
    )
    first, second = outcome.plan.items
    assert first.target_room == "401"
    assert second.target_room is None
    assert (second.check_in_date.isoformat(), second.check_out_date.isoformat()) == (
        "2026-10-14", "2026-10-16",
    )
    past = _verify(
        "上个月住过", [_item(quote="上个月住过", check_in_date="2026-09-01",
                             check_out_date="2026-09-02")]
    )
    assert past.plan.items[0].check_in_date is None


def test_only_requests_register() -> None:
    """只有申请：登记。"""
    text = "请送两条毛巾"
    resolution = resolve_task_request(plan_for(text, ("service_request", text)), text)
    assert resolution.register
    assert resolution.task_type is None


def test_request_then_withdraw_of_the_same_item_does_not_register() -> None:
    """申请后同事项撤回：不登记，撤回事项交给管家通知。"""
    text = "请送两条毛巾，算了，毛巾不用送了"
    plan = plan_for(
        text,
        ("service_request", "请送两条毛巾", {"subject": "毛巾"}),
        ("request_withdraw", "毛巾不用送了", {"subject": "毛巾", "withdraws": 1}),
    )
    resolution = resolve_task_request(plan, text)
    assert not resolution.register
    assert resolution.withdrawn == ("毛巾",)


def test_merged_batch_uses_the_same_order_rule() -> None:
    """跨消息合并的冻结正文以换行连接，顺序结论与单句一致。"""
    text = "请送毛巾\n不用送毛巾了"
    plan = plan_for(
        text,
        ("service_request", "请送毛巾"),
        ("request_withdraw", "不用送毛巾了", {"withdraws": 1}),
    )
    assert not resolve_task_request(plan, text).register


def test_withdraw_before_a_new_request_keeps_the_new_request() -> None:
    """撤回在前、本计划内没有更早申请：撤回对象不在本计划，后面的申请独立登记。"""
    text = "不用送毛巾了，麻烦送两瓶水"
    plan = plan_for(
        text,
        ("request_withdraw", "不用送毛巾了", {"subject": "毛巾"}),
        ("service_request", "麻烦送两瓶水", {"subject": "水"}),
    )
    resolution = resolve_task_request(plan, text)
    assert resolution.register
    assert resolution.subjects == ("水",)
    assert resolution.withdrawn == ("毛巾",)


def test_unlinked_withdraw_after_a_request_asks_for_confirmation() -> None:
    """本计划内有更早申请，撤回却没给出关联：无法可靠关联，不登记并转确认。"""
    text = "请送毛巾，那个不用了"
    plan = plan_for(text, ("service_request", "请送毛巾"), ("request_withdraw", "那个不用了"))
    resolution = resolve_task_request(plan, text)
    assert not resolution.register
    assert resolution.ask_confirm


def test_repeated_wording_registers_the_final_request() -> None:
    """「请送毛巾，不用送毛巾了，还是请送毛巾」：撤回指向第一项，最后一项是新申请。"""
    text = "请送毛巾，不用送毛巾了，还是请送毛巾"
    plan = plan_for(
        text,
        ("service_request", "请送毛巾", {"start": 0}),
        ("request_withdraw", "不用送毛巾了", {"withdraws": 1}),
        ("service_request", "请送毛巾", {"start": 14}),
    )
    resolution = resolve_task_request(plan, text)
    assert [item.start for item in plan.plan.items] == [0, 5, 14]
    assert resolution.register


def test_repeated_wording_with_an_unverifiable_start_asks_for_confirmation() -> None:
    """重复措辞但 start 无法唯一核验：相关项无效，有撤回时转确认，不猜测。"""
    text = "请送毛巾，不用送毛巾了，还是请送毛巾"
    plan = plan_for(
        text,
        ("service_request", "请送毛巾", {"start": 0}),
        ("request_withdraw", "不用送毛巾了", {"withdraws": 1}),
        ("service_request", "请送毛巾", {"start": 13}),
    )
    resolution = resolve_task_request(plan, text)
    assert not resolution.register
    assert resolution.ask_confirm


def test_an_invalid_withdraw_never_falls_back_to_the_request() -> None:
    """撤回项位置失效：同轮有申请项就转确认，失效撤回不被通用过滤删掉。"""
    text = "请送毛巾，不用了"
    plan = plan_for(
        text, ("service_request", "请送毛巾"), ("request_withdraw", "不需要", {"start": 5})
    )
    resolution = resolve_task_request(plan, text)
    assert not resolution.register
    assert resolution.ask_confirm


@pytest.mark.parametrize(
    "text",
    ["上次请帮我送两条毛巾", "房间空调没有故障，不用维修", "不用帮我订房了", "上次请帮我订房"],
)
def test_history_and_negation_never_register_despite_lexical_signals(text: str) -> None:
    """计划判为历史或否定时不登记，即使词面服务或订房信号为真。"""
    resolution = resolve_task_request(plan_for(text, ("history_mention", text)), text)
    assert not resolution.register
    assert not resolution.ask_confirm
    assert not resolution.safety_tip


def test_only_unclear_items_ask_for_confirmation() -> None:
    """只有 unclear：不登记；像申请时回确认话术，信息问题不回「需要安排什么」。"""
    text = "麻烦送一下那个"
    resolution = resolve_task_request(plan_for(text, ("unclear", text)), text)
    assert not resolution.register
    assert resolution.ask_confirm
    question = "它能烘干吗"
    info = resolve_task_request(plan_for(question, ("unclear", question)), question)
    assert not info.register
    assert not info.ask_confirm


def test_failed_planning_never_creates_service_tasks() -> None:
    """规划失败：服务类不新建任务，词面命中只回确认（D13）；设施给安全提示并确认（D14）。"""
    service = resolve_task_request(failed_plan("请补两瓶矿泉水", "timeout"), "请补两瓶矿泉水")
    assert (service.register, service.ask_confirm, service.safety_tip) == (False, True, False)
    fault = resolve_task_request(None, "空调坏了")
    assert (fault.register, fault.ask_confirm, fault.safety_tip) == (False, True, True)
    assert not resolve_task_request(None, "早餐几点").ask_confirm


def test_a_plan_for_another_text_counts_as_no_plan() -> None:
    """计划摘要与本轮正文不一致时按无计划处理，不复用错误计划。"""
    plan = plan_for("请补两瓶矿泉水", ("service_request", "请补两瓶矿泉水"))
    resolution = resolve_task_request(plan, "请补两瓶矿泉水吧")
    assert not resolution.register
    assert resolution.ask_confirm


def test_task_type_follows_existing_rules() -> None:
    """多事项或订房意向为特殊服务，单个当前设施故障为维修。"""
    text = "空调坏了，再送两瓶水"
    both = plan_for(text, ("facility_fault", "空调坏了"), ("service_request", "再送两瓶水"))
    assert resolve_task_request(both, text).task_type is BusinessTaskType.SPECIAL_SERVICE
    fault = plan_for("空调坏了", ("facility_fault", "空调坏了"))
    resolution = resolve_task_request(fault, "空调坏了")
    assert resolution.task_type is BusinessTaskType.MAINTENANCE
    assert resolution.current_fault and resolution.safety_tip
    booking = plan_for("帮我订102", ("booking_request", "帮我订102"))
    assert resolve_task_request(booking, "帮我订102").task_type is (
        BusinessTaskType.SPECIAL_SERVICE
    )


def test_agitated_is_the_only_reviewable_handoff_reason() -> None:
    """情绪词按计划复核：非投诉去除、投诉保留、规划失败保留；退款等硬理由不受计划影响。"""
    text = "第一次来太开心了!!!"
    calm = plan_for(text, ("chitchat", text))
    angry = plan_for(text, ("chitchat", text, {"risk": "complaint"}))
    assert resolve_handoff_reason(text, calm) is None
    assert resolve_handoff_reason(text, angry) == "agitated"
    assert resolve_handoff_reason(text, failed_plan(text, "timeout")) == "agitated"
    assert resolve_handoff_reason(text) == "agitated"
    refund = "我要退款!!!"
    assert resolve_handoff_reason(refund, plan_for(refund, ("chitchat", refund))) == "refund"


@pytest.mark.parametrize("risk", [[], {}, 1, True, ["complaint"], {"x": 1}, "angry"])
def test_malformed_risk_types_fail_the_plan_without_raising(risk) -> None:
    """B24-R5：风险字段是数组、对象、数值、布尔或未知字符串时整份计划失败，不抛异常。"""
    outcome = _verify("请帮我送水", [_item(kind="service_request", quote="请帮我送水", risk=risk)])
    assert outcome.status == "failed"


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": ["service_request"]},
        {"kind": {"a": 1}},
        {"id": "1"},
        {"withdraws": "1", "kind": "request_withdraw"},
        {"target_room": ["401"]},
        {"check_in_date": ["2026-10-08"], "check_out_date": {"d": 1}},
        {"question": ["x"], "subject": {"y": 1}},
        {"start": "0"},
    ],
)
def test_any_json_shape_yields_ok_or_failed(fields: dict) -> None:
    """B24-R5：同一 JSON 边界的其他字段任何形状都只得到 ok 或 failed。"""
    item = _item(kind="service_request", quote="请帮我送水")
    item.update(fields)
    assert _verify("请帮我送水", [item]).status in {"ok", "failed"}


def test_hard_reason_in_any_text_variant_beats_agitated_review() -> None:
    """B24-R2：情绪词先命中，但去空白后的变体里有「退款」：硬理由优先，计划不能清除。"""
    text = "第一次来太开心了!!! 退\n款"
    assert resolve_handoff_reason(text, plan_for(text, ("chitchat", text))) == "refund"
    assert resolve_handoff_reason(text) == "refund"
