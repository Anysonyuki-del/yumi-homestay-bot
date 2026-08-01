from homestay_bot.services.complaint_service import ComplaintClassification, ComplaintService


def test_complaint_service_classifies_refund_and_platform_escalation() -> None:
    """退款和平台介入应进入最高风险客诉模式。"""
    service = ComplaintService()

    result = service.classify("我要退款，不处理我就找平台投诉")

    assert isinstance(result, ComplaintClassification)
    assert result.is_complaint is True
    assert result.reason == "refund"
    assert result.risk_level == "critical"
    assert result.refund_or_compensation is True


def test_complaint_service_ignores_normal_feedback() -> None:
    """普通建议不能误触发人工客诉模式。"""
    result = ComplaintService().classify("希望房间可以多放两瓶矿泉水")

    assert result.is_complaint is False


def test_complaint_service_uses_fixed_warm_acknowledgement() -> None:
    """客诉安抚必须使用已确认的固定文案。"""
    assert ComplaintService.guest_acknowledgement() == (
        "我已收到您的诉求，正在火速通知管家，麻烦您稍作等待，"
        "我们的管家了解情况后一定会为您解决问题"
    )
