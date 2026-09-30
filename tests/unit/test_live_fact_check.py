"""一句多问的关键事实核对：改错删句、漏掉补原文、正常转述不误伤（Codex 审查 R1、R2）。"""

from homestay_bot.services.live_fact_check import check_integrated_reply

WEATHER = "明天武汉阴有阵雨，15～21℃。"
HOURS = "黄鹤楼今天开放时间为08:30～18:00。"


def test_reversed_range_sentence_is_dropped_and_the_group_marked_missing() -> None:
    """区间写反：只删那一句，同段其他句子保留；该组要补回原文。"""
    result = check_integrated_reply("明天21～15℃注意保暖。江滩适合看夜景。", [WEATHER])

    assert result.text == "江滩适合看夜景。"
    assert result.missing == (0,)


def test_omitted_opening_hours_are_detected() -> None:
    """没有单位的营业时间被漏掉也能发现。"""
    assert check_integrated_reply("可以去东湖绿道骑行。", [HOURS]).missing == (0,)


def test_faithful_rephrasing_is_not_flagged() -> None:
    """拆成最高最低、换一种时间写法、知识库里的其他时间，都不算错也不算漏。"""
    for reply, live in [
        ("明天最高21℃、最低15℃。东湖可以骑行。", WEATHER),
        ("黄鹤楼8:30到18:00开放。", HOURS),
        ("巷口热干面早上6:30开门。明天15～21℃。", WEATHER),
    ]:
        result = check_integrated_reply(reply, [live])
        assert result.text == reply and result.missing == ()
