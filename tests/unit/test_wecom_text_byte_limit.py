"""客人回复不超过企业微信文本消息的 2048 字节上限：超长时拆段，段内再收口。"""

import pytest
from unit.test_deepseek_client import _respond_with

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import (
    GUEST_REPLY_MAX_PARTS,
    WECOM_TEXT_MAX_BYTES,
    fit_wecom_text,
    fits_guest_reply_parts,
    split_guest_reply,
)
from homestay_bot.services.knowledge_service import KnowledgeSnippet

FOOTER = "这是我今天（9月23日）帮您查到的最新出行信息。出行信息可能临时变化，出发前可以再确认一下。"


def _size(text: str) -> int:
    """按企业微信口径计算 UTF-8 字节数。"""
    return len(text.encode("utf-8"))


def test_short_text_is_unchanged() -> None:
    """未超限的回复原样返回。"""
    text = "早餐在一楼。\n\n出门记得带伞。"

    assert fit_wecom_text(text) == text


def test_long_reply_is_cut_at_a_sentence_end_and_keeps_the_footer() -> None:
    """超限时在句末截断，结尾的时效说明整段保留。"""
    body = "黄鹤楼可以俯瞰长江大桥与三镇，傍晚的光线最好。" * 60
    text = f"{body}\n\n{FOOTER}"

    fitted = fit_wecom_text(text)

    assert _size(fitted) <= WECOM_TEXT_MAX_BYTES
    assert fitted.endswith(FOOTER)
    head = fitted[: -len(FOOTER)].rstrip()
    assert head.endswith("。")
    assert text.startswith(head)


def test_cut_without_a_nearby_sentence_end_is_marked() -> None:
    """找不到合适的句末时按字符截断，并用省略号标明被截断。"""
    text = "长" * 1000

    fitted = fit_wecom_text(text)

    assert _size(fitted) <= WECOM_TEXT_MAX_BYTES
    assert fitted.endswith("…")


@pytest.mark.parametrize("limit", [10, 11, 12])
def test_multibyte_characters_are_never_split(limit: int) -> None:
    """按字节截断不会切开一个汉字。"""
    fitted = fit_wecom_text("汉字汉字汉字汉字", max_bytes=limit)

    assert _size(fitted) <= limit
    assert fitted.encode("utf-8").decode("utf-8") == fitted


@pytest.mark.asyncio
async def test_audited_answer_beyond_the_part_limit_is_not_truncated() -> None:
    """审核答案拆到段数上限仍放不下时不截断条件，改为未确认并请客人细化。"""
    oversized = KnowledgeSnippet(
        source_id=1,
        category="客房温控",
        question="房间能调节温度吗？",
        answer="空调可在18至30摄氏度之间调节。" + "另有若干使用条件。" * 200,
    )

    decision, _ = await _respond_with(
        "屋里闷热的话能自己调凉一点吗？",
        "可以调到10摄氏度。",
        [oversized],
    )

    assert oversized.answer not in decision.reply_text
    assert "尚未确认" in decision.reply_text
    assert decision.knowledge_gap is True


@pytest.mark.asyncio
async def test_long_audited_answer_within_the_limit_is_answered_in_full() -> None:
    """超过单条上限但能拆成几段的审核答案照常完整回答，发送时再拆段。"""
    long_answer = KnowledgeSnippet(
        source_id=1,
        category="客房温控",
        question="房间能调节温度吗？",
        answer="空调可在18至30摄氏度之间调节。" + "另有若干使用条件。" * 100,
    )

    decision, _ = await _respond_with(
        "屋里闷热的话能自己调凉一点吗？",
        "可以调到10摄氏度。",
        [long_answer],
    )

    assert decision.reply_text == long_answer.answer
    assert _size(long_answer.answer) > WECOM_TEXT_MAX_BYTES


def _strip_labels(parts: list[str]) -> str:
    """去掉段序号与空白后拼回，用来核对拆段不增删正文。"""
    import re

    return re.sub(r"\s", "", "".join(re.sub(r"^（\d/\d）", "", part) for part in parts))


def test_short_reply_is_not_split() -> None:
    """放得进一条的回复原样返回，不加序号。"""
    assert split_guest_reply("早餐在一楼。", Language.ZH) == ["早餐在一楼。"]


def test_long_reply_is_split_by_paragraph_with_labels_and_no_lost_text() -> None:
    """按段落拆开，每段都在上限内、带序号，拼回后正文一字不差。"""
    paragraph = "黄鹤楼可以俯瞰长江大桥与三镇，傍晚的光线最好。" * 20
    text = "\n\n".join([paragraph, paragraph, paragraph])

    parts = split_guest_reply(text, Language.ZH)

    assert len(parts) == 3
    assert [part[:5] for part in parts] == ["（1/3）", "（2/3）", "（3/3）"]
    assert all(_size(part) <= WECOM_TEXT_MAX_BYTES for part in parts)
    assert _strip_labels(parts) == text.replace("\n", "")


def test_footer_stays_in_the_last_part() -> None:
    """时效说明只出现在最后一段。"""
    paragraph = "黄鹤楼可以俯瞰长江大桥与三镇，傍晚的光线最好。" * 20
    text = f"{paragraph}\n\n{paragraph}\n\n{FOOTER}"

    parts = split_guest_reply(text, Language.ZH)

    assert parts[-1].endswith(FOOTER)
    assert all(FOOTER not in part for part in parts[:-1])


def test_a_single_huge_sentence_is_split_without_breaking_characters() -> None:
    """单句超长时按字符切开，每段都是完整的 UTF-8 文本。"""
    parts = split_guest_reply("长" * 1400, Language.ZH)

    assert all(_size(part) <= WECOM_TEXT_MAX_BYTES for part in parts)
    assert _strip_labels(parts) == "长" * 1400


def test_parts_beyond_the_limit_are_capped_and_keep_the_footer() -> None:
    """普通回复超过段数上限时保留前几段，最后一段收口并保留时效说明。"""
    paragraph = "黄鹤楼可以俯瞰长江大桥与三镇，傍晚的光线最好。" * 20
    text = "\n\n".join([paragraph] * 5 + [FOOTER])

    parts = split_guest_reply(text, Language.ZH)

    assert len(parts) == GUEST_REPLY_MAX_PARTS
    assert parts[-1].endswith(FOOTER)
    assert all(_size(part) <= WECOM_TEXT_MAX_BYTES for part in parts)


def test_english_parts_use_english_labels() -> None:
    """英文回复的序号用半角括号。"""
    text = "\n\n".join(["The riverside walk is lovely at dusk. " * 30] * 2)

    parts = split_guest_reply(text, Language.EN)

    assert parts[0].startswith("(1/")


def test_fits_check_matches_the_reply_length_cap() -> None:
    """能否完整发出：超过 1500 字不行，能拆进段数上限的可以。"""
    assert fits_guest_reply_parts("短回复。")
    assert fits_guest_reply_parts("黄鹤楼。" * 300)
    assert not fits_guest_reply_parts("长" * 1501)
