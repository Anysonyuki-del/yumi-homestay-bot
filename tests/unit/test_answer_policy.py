import pytest

from homestay_bot.services.answer_policy import (
    facility_fault_exclusion,
    handoff_reason,
    has_facility_fault_signal,
    is_homestay_related,
    is_property_specific,
    is_service_request,
    is_transaction_sensitive,
)


def test_transaction_questions_are_detected() -> None:
    """价格、房态和售后交易问题必须进入交易安全边界。"""
    assert is_transaction_sensitive("今天还有房吗？")
    assert is_transaction_sensitive("这个订单能退款多少？")
    assert is_transaction_sensitive("可以取消或改期吗？")
    assert is_transaction_sensitive("付款后多久确认？")
    assert not is_transaction_sensitive("武汉地铁一般几点停运？")


def test_property_specific_questions_are_detected() -> None:
    """设施、服务和民宿距离属于专属事实。"""
    assert is_property_specific("你们有停车场吗？")
    assert is_property_specific("提供早餐和宠物用品吗？")
    assert is_property_specific("民宿离黄鹤楼有多远？")
    assert is_property_specific("可以寄存行李吗？")
    assert not is_property_specific("武汉有哪些地方好玩？")


def test_transaction_has_priority_over_property_specific() -> None:
    """发票金额属于交易问题，即使也涉及民宿服务。"""
    text = "你们能开多少钱的发票？"
    assert is_transaction_sensitive(text)
    assert is_property_specific(text)


def test_high_risk_requests_have_deterministic_handoff_reason() -> None:
    """价格、退款、投诉、提前入住和激烈情绪必须由本地规则要求接管。"""
    assert handoff_reason("这个房间最低多少钱？") == "price"
    assert handoff_reason("我要退款") == "refund"
    assert handoff_reason("我要投诉你们") == "complaint"
    assert handoff_reason("我想提前入住") == "early_check_in"
    assert handoff_reason("太离谱了，你们必须马上解决！！！") == "agitated"
    assert handoff_reason("武汉有哪些地方好玩？") is None
    assert handoff_reason("武汉地铁票价多少钱？") is None


def test_unrelated_questions_are_rejected_locally() -> None:
    """客服只承接民宿住宿和武汉旅行相关问题。"""
    assert is_homestay_related("房间几点可以入住？")
    assert is_homestay_related("武汉有哪些地方好玩？")
    assert is_homestay_related("好的，两个人")
    assert not is_homestay_related("帮我写一段股票量化交易程序")


@pytest.mark.parametrize(
    "text",
    [
        "洗衣机好像也出了点问题",
        "灯不亮了",
        "马桶堵了",
        "窗帘拉不动了",
        "烘干机不工作",
        "冰箱坏了",
        "The dryer is not working",
    ],
)
def test_open_facility_fault_is_a_service_request(text: str) -> None:
    """新设施首次出现时也应由开放故障信号进入维修服务。"""
    assert has_facility_fault_signal(text)
    assert facility_fault_exclusion(text) is None
    assert is_service_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "这个设计有点问题",
        "洗衣机怎么用？",
        "订单好像有问题",
        "房间卫生有点问题",
        "我身体有点问题",
    ],
)
def test_facility_fault_does_not_match_abstract_problem_or_usage_question(
    text: str,
) -> None:
    """抽象问题或设施用法咨询不得误建维修任务。"""
    assert not has_facility_fault_signal(text)


@pytest.mark.parametrize(
    ("text", "scope"),
    [
        ("我的手机坏了", "private"),
        ("手机坏了", "private"),
        ("我自己带来的咖啡机不工作", "private"),
        ("景区的灯坏了", "external"),
        ("商场电梯打不开", "external"),
    ],
)
def test_private_or_external_fault_is_excluded_from_homestay_maintenance(
    text: str,
    scope: str,
) -> None:
    """明确属于私人物品或外部场所的故障不得授权民宿维修任务。"""
    assert has_facility_fault_signal(text)
    assert facility_fault_exclusion(text) == scope
    assert not is_service_request(text)


def test_room_facility_wins_over_ambiguous_official_channel_context() -> None:
    """客人明确说房间设施时必须留在民宿维修边界。"""
    text = "房间里的灯不亮了"

    assert has_facility_fault_signal(text)
    assert facility_fault_exclusion(text) is None
    assert is_service_request(text)
