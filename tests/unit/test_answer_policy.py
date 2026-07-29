from homestay_bot.services.answer_policy import (
    is_property_specific,
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
