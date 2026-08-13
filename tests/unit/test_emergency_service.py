import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.emergency_service import EmergencyService


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("门锁坏了，我进不了房间", "access"),
        ("房间着火了", "fire"),
        ("闻到燃气味", "gas"),
        ("有人触电了", "electric"),
        ("有人威胁要打我", "violence"),
        ("客人突然昏迷，需要急救", "medical"),
        ("The door lock is broken and I cannot get in", "access"),
        ("There is a fire in the room", "fire"),
        ("I smell gas", "gas"),
        ("Someone got an electric shock", "electric"),
        ("I am being threatened", "violence"),
        ("We need a medical emergency response", "medical"),
    ],
)
def test_classify_emergency_in_chinese_and_english(
    text: str, category: str
) -> None:
    """确定性规则应覆盖中英文入住安全紧急事件。"""
    result = EmergencyService().classify(text)

    assert result.is_emergency is True
    assert result.category == category


def test_emergency_reply_uses_fixed_safety_message() -> None:
    """紧急事件回复应来自固定模板，不能交由模型自由编写。"""
    service = EmergencyService()

    result = service.classify("房间着火了")

    zh_reply = service.safety_reply(result, Language.ZH)
    en_reply = service.safety_reply(result, Language.EN)

    assert "119" in zh_reply
    assert zh_reply.endswith("我会立即联系管家来处理，请您稍等。")
    assert "已收到" not in zh_reply
    assert "leave" in en_reply.lower()
    assert "has been alerted" not in en_reply.lower()
