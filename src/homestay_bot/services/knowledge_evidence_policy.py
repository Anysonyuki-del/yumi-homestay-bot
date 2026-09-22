"""静态本店问答的证据计划：问题问到哪些主题的哪些属性，审核答案能否覆盖。

证据门原先只核对「问题的主题词是否出现在答案里」，于是「早餐几点送到」这条
答案可以给「早餐能做无麸质吗」背书。本模块把问题拆成「主题 + 所问属性」，
答案必须覆盖每一个主题的每一个属性才算有证据。

ponytail: 属性与主题都是有限的人工清单，不做语义蕴含。识别不出的限定、
无法绑定的数值、冲突答案一律按证据不足处理，不退回「主题词存在即可」。
相似度、分类和问题标题只用于定位候选，任何情况下都不作事实证据。
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from homestay_bot.services.knowledge_service import (
    PropertyTopic,
    detect_property_topics,
    normalize_text,
)

# 审核答案原文直接发给客人，超过这个长度就请客人细化问题，不截断条件与例外。
STATIC_REPLY_MAX_CHARS = 1_000

PlanStatus = Literal["grounded", "insufficient", "unclear", "general"]

_CLAUSE_SPLIT = re.compile(r"[，,。；;！？!?\n]")

# 所问属性。顺序无关，一个问题可以同时问多个属性。
_ATTRIBUTE_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "time",
        re.compile(
            r"几点|什么时候|何时|时间|时段|多久|多长时间|营业|开放"
            r"|\bwhen\b|what\s+time|\bhours\b|how\s+long",
            re.IGNORECASE,
        ),
    ),
    (
        "fee",
        re.compile(
            r"收费|费用|多少钱|价格|要钱|免费|另收|加钱|押金"
            r"|how\s+much|\bfee\b|\bcost\b|\bfree\b|\bcharge",
            re.IGNORECASE,
        ),
    ),
    (
        "location",
        re.compile(
            r"在哪|哪里|哪儿|位置|放在|怎么走|几楼|楼层"
            r"|\bwhere\b|which\s+floor|how\s+do\s+i\s+get",
            re.IGNORECASE,
        ),
    ),
    (
        "operation",
        re.compile(
            r"怎么用|如何使用|怎么开|怎么关|怎么调|能调|可以调|调到|调节|设置|操作"
            r"|自己(?:调|开|关|弄)|调(?:凉|暖|高|低)"
            r"|how\s+(?:do|can)\s+i\s+(?:use|turn|adjust|set)|\badjust\b",
            re.IGNORECASE,
        ),
    ),
)

# 特殊能力与限制：问题里出现这些限定词时，答案必须点名同一个限定词，
# 同主题的其他方面（送餐时间、普通床位）都不算证据。
_SPECIAL_QUALIFIERS: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = (
    (
        "gluten_free",
        re.compile(r"无麸质|不含麸质|麸质过敏|gluten[-\s]?free", re.IGNORECASE),
        re.compile(r"无麸质|不含麸质|麸质|gluten", re.IGNORECASE),
    ),
    (
        "vegetarian",
        re.compile(r"素食|全素|纯素|不吃肉|vegetarian|vegan", re.IGNORECASE),
        re.compile(r"素食|全素|纯素|vegetarian|vegan", re.IGNORECASE),
    ),
    (
        "halal",
        re.compile(r"清真|穆斯林|halal", re.IGNORECASE),
        re.compile(r"清真|halal", re.IGNORECASE),
    ),
    (
        "allergy",
        re.compile(r"过敏原?|忌口|allerg", re.IGNORECASE),
        re.compile(r"过敏|忌口|allerg", re.IGNORECASE),
    ),
    (
        "infant",
        re.compile(r"婴儿|宝宝|婴儿床|baby|infant|\bcot\b|crib", re.IGNORECASE),
        re.compile(r"婴儿|宝宝|婴儿床|baby|infant|\bcot\b|crib", re.IGNORECASE),
    ),
    (
        # 问的是能不能延迟退房，答案讲「退房当日可寄存行李」不算数：必须讲退房
        # 时间本身或延迟退房的规则。提前入住同理。
        "late_checkout",
        re.compile(
            r"延迟退房|晚一?点退房|退房.{0,6}(?:晚|延|推迟)|late\s+check-?\s?out"
            r"|check\s?out\s+(?:at|by)\s*\d"
            # 「离店当天能不能多待会儿」同样是在问延迟退房。
            r"|离店当(?:天|日).{0,10}(?:待|留|住|用)"
            r"|keep\s+the\s+room\s+until|on\s+departure\s+day",
            re.IGNORECASE,
        ),
        re.compile(
            r"退房时间|延迟退房|退房为|退房是|退房不?晚于|late\s+check-?\s?out"
            r"|check-?\s?out\s+(?:time|is|at|before|by)",
            re.IGNORECASE,
        ),
    ),
    (
        "early_checkin",
        re.compile(
            r"提前入住|早一?点入住|入住.{0,6}(?:提前|早)|early\s+check-?\s?in"
            r"|check\s?in\s+(?:at|by)\s*\d",
            re.IGNORECASE,
        ),
        re.compile(
            r"入住时间|提前入住|入住为|入住是|early\s+check-?\s?in"
            r"|check-?\s?in\s+(?:time|is|at|from)",
            re.IGNORECASE,
        ),
    ),
    (
        # 「晚上还有热水吗」「半夜会不会停」问的是夜间供应时段，设备位置和出水
        # 速度回答不了；答案必须讲供应时段或是否限时。
        "night_supply",
        re.compile(
            r"(?:晚上|夜里|夜间|半夜|深夜|凌晨|\d{1,2}\s*点多?)[^，。？?]{0,12}"
            r"(?:还有|有没有|能不能|会不会|停|限时|供应)"
            r"|会不会[^，。？?]{0,6}停"
            r"|(?:at\s+night|late\s+at\s+night|after\s+midnight)[^.?]{0,20}"
            r"(?:still|available|hot\s+water|run\s+out)",
            re.IGNORECASE,
        ),
        re.compile(
            r"24\s*小时|全天|不间断|夜间|通宵|限时|至\d{1,2}\s*[:：]\d{2}"
            r"|\d{1,2}\s*[:：]\d{2}\s*(?:至|到|-|—)\s*\d{1,2}\s*[:：]\d{2}"
            r"|停止供应|24\s*hours|overnight|around\s+the\s+clock",
            re.IGNORECASE,
        ),
    ),
    (
        # 自助洗衣、烘干、代洗是三种不同的服务。洗衣房的开放时间证明不了有烘干机，
        # 自助洗衣也证明不了提供代洗。
        "drying",
        re.compile(r"烘干|甩干|烘衣|tumble\s?dry|dryer", re.IGNORECASE),
        re.compile(r"烘干|甩干|烘衣|烘干机|洗烘|tumble\s?dry|dryer", re.IGNORECASE),
    ),
    (
        "laundry_service",
        re.compile(
            r"代洗|送洗|洗衣服务|干洗|帮.{0,4}洗衣服|(?:送|拿).{0,6}衣服.{0,4}洗"
            r"|laundry\s+service|dry\s+clean|wash\s+and\s+fold",
            re.IGNORECASE,
        ),
        re.compile(
            r"代洗|送洗|洗衣服务|干洗|laundry\s+service|dry\s+clean|wash\s+and\s+fold",
            re.IGNORECASE,
        ),
    ),
    # 同一主题下的具体设施：问的是滑梯，答的是餐椅，同样不算有证据。这是有限
    # 清单，只覆盖已知会被混淆的对象，识别不出的具体设施仍按证据不足处理。
    (
        "playground",
        re.compile(r"滑梯|游乐区|游乐场|playground|play\s?area", re.IGNORECASE),
        re.compile(r"滑梯|游乐区|游乐场|playground|play\s?area", re.IGNORECASE),
    ),
    (
        "pool",
        re.compile(r"泳池|游泳池|swimming\s+pool", re.IGNORECASE),
        re.compile(r"泳池|游泳池|swimming\s+pool", re.IGNORECASE),
    ),
    (
        "gym",
        re.compile(r"健身房|健身器材|\bgym\b|fitness\s+room", re.IGNORECASE),
        re.compile(r"健身房|健身器材|\bgym\b|fitness\s+room", re.IGNORECASE),
    ),
    (
        "bathtub",
        re.compile(r"浴缸|泡澡|bathtub", re.IGNORECASE),
        re.compile(r"浴缸|泡澡|bathtub", re.IGNORECASE),
    ),
    (
        "projector",
        re.compile(r"投影(?:仪|机)?|projector", re.IGNORECASE),
        re.compile(r"投影(?:仪|机)?|projector", re.IGNORECASE),
    ),
    (
        "mahjong",
        re.compile(r"麻将|mahjong", re.IGNORECASE),
        re.compile(r"麻将|mahjong", re.IGNORECASE),
    ),
)

_TIME_EVIDENCE = re.compile(
    # 中文口语常写「下午三点后入住」，不能只认阿拉伯数字。
    r"\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*(?:点|时)(?!间)|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)"
    r"|[零一两二三四五六七八九十]{1,3}\s*点"
    r"|(?:上午|中午|下午|傍晚|晚上|凌晨|早上)"
    r"|全天|24\s*小时|24\s*hours|随时|any\s?time|noon|midnight",
    re.IGNORECASE,
)
_LOCATION_EVIDENCE = re.compile(
    r"在|位于|旁(?:边)?|楼|层|柜|门口|入口|大厅|架|室|区"
    r"|\bon\b|\bin\b|\bnext\s+to\b|\bbeside\b|floor|lobby|entrance|rack|shelf",
    re.IGNORECASE,
)
_OPERATION_EVIDENCE = re.compile(
    r"调(?:节|到|整|高|低)|可调|设置|操作|使用|开关|按(?:钮|键)?|遥控|面板|旋钮"
    r"|\badjust\b|\bset\b|\bswitch\b|\bpanel\b|\bremote\b|\bcontrol\b|\buse\b",
    re.IGNORECASE,
)

# 审核知识只是数据。正文里出现指令式内容时不能原样转发给客人。
_INSTRUCTION_INJECTION = re.compile(
    r"忽略(?:以上|上述|前面|之前).{0,12}(?:指令|要求|规则)|系统提示词?|开发者模式"
    r"|ignore\s+(?:all\s+)?(?:previous|above)\s+instructions?|system\s+prompt"
    r"|api[_\s-]?key|请调用|调用工具",
    re.IGNORECASE,
)

# 指代不清：客人用「那个」「这东西」指房里的某样东西，检索也确实找到了候选，
# 但无法确认问的是哪一项。只在这种情况下澄清，普通旅游和通用问题不受影响。
_VAGUE_REFERENCE = re.compile(
    r"那个|这个|那东西|这东西|那玩意|它(?:能|会|怎么|好用)"
    r"|that\s+(?:thing|one)|this\s+(?:thing|one)",
    re.IGNORECASE,
)

_AFFIRMATIVE_FEE_FREE = re.compile(
    r"免费|不收费|不另?收|no\s+charge|free\s+of\s+charge",
    re.IGNORECASE,
)
_PAID_FEE = re.compile(r"收费|每(?:天|次|小时|晚)\s*\d|\d+\s*元|charged?|\bfee\b", re.IGNORECASE)

# 澄清语：同一会话里只问一次，第二次直接给未确认回复。
CLARIFY_REPLY_ZH = "请问您想了解房间的哪项设施或哪项入住安排？说得具体一些我才好确认。"
CLARIFY_REPLY_EN = (
    "Which facility or part of your stay would you like to know about? "
    "With a bit more detail I can confirm it for you."
)
_CLARIFY_MARKERS = (
    "哪项设施",
    "Which facility or part of your stay",
)


@dataclass(frozen=True)
class EvidencePlan:
    """一次静态问答的证据结论。

    `general` 表示不是本店静态问题，交回原有通用、旅游与服务分支处理。
    """

    status: PlanStatus
    topics: tuple[PropertyTopic, ...]
    answers: tuple[str, ...]
    reason: str

    @property
    def handles_reply(self) -> bool:
        """本计划是否接管最终回复。"""
        return self.status != "general"


def asked_attributes(question_text: str, topic: PropertyTopic | None = None) -> set[str]:
    """返回问题（或问题中点名该主题的分句）问到的属性。

    多主题问句按分句限定：「早餐几点送到？另外停车怎么收费？」里，时间只属于
    早餐，费用只属于停车，不互相套用。
    """
    clauses = [item for item in _CLAUSE_SPLIT.split(question_text) if item.strip()]
    if topic is not None:
        scoped = [item for item in clauses if topic.aliases.search(normalize_text(item))]
        if scoped:
            clauses = scoped
    attributes = {
        name
        for name, pattern in _ATTRIBUTE_QUESTION_PATTERNS
        for clause in clauses
        if pattern.search(clause)
    }
    attributes.update(
        f"special:{name}"
        for name, question_pattern, _ in _SPECIAL_QUALIFIERS
        for clause in clauses
        if question_pattern.search(clause)
    )
    # 没问具体属性时，只要答案讲到该主题就算覆盖（「有停车位吗」）。
    return attributes or {"existence"}


def _covers_attribute(attribute: str, answer: str) -> bool:
    """判断一条审核答案是否覆盖某个属性。"""
    if attribute == "existence":
        return True
    if attribute == "time":
        return _TIME_EVIDENCE.search(answer) is not None
    if attribute == "location":
        return _LOCATION_EVIDENCE.search(answer) is not None
    if attribute == "operation":
        return _OPERATION_EVIDENCE.search(answer) is not None
    if attribute == "fee":
        # 费用证据由主题级的收费校验完成（同一条问答里要有本店费用说明）。
        return True
    if attribute.startswith("special:"):
        name = attribute.split(":", 1)[1]
        for qualifier, _, evidence_pattern in _SPECIAL_QUALIFIERS:
            if qualifier == name:
                return evidence_pattern.search(answer) is not None
    return False


def _conflicting_fee_claims(answers: Sequence[str]) -> bool:
    """多条答案对同一问题一方说免费、一方说收费时不投票，按未确认处理。"""
    if len(answers) < 2:
        return False
    free = any(_AFFIRMATIVE_FEE_FREE.search(item) for item in answers)
    paid = any(_PAID_FEE.search(item) for item in answers)
    return free and paid


def already_clarified(messages: Sequence[dict[str, str]]) -> bool:
    """会话里是否已经发出过澄清提问。"""
    return any(
        item.get("role") == "assistant"
        and any(marker in str(item.get("content", "")) for marker in _CLARIFY_MARKERS)
        for item in messages
    )


def build_evidence_plan(
    question_text: str,
    knowledge: Sequence[Any],
    *,
    supporting_for_topic: Callable[[PropertyTopic, str, list[Any]], list[Any]],
    is_property_question: bool,
) -> EvidencePlan:
    """给出本轮静态问答的证据计划。

    `supporting_for_topic` 复用证据门已有的范围与收费校验，本模块只在其之上
    追加属性核对，避免两处各自判断主题导致结论不一致。
    """
    entries = list(knowledge)
    topics = tuple(detect_property_topics(question_text))
    if not topics:
        qualifiers = {
            item for item in asked_attributes(question_text) if item.startswith("special:")
        }
        if qualifiers:
            # 问题点名了具体限定（健身房、无麸质、延迟退房），本身就说明在问本店。
            # 主题清单认不出时，直接要求审核答案点名同一个限定，不放行模型断言。
            covering = next(
                (
                    item
                    for item in entries
                    if all(_covers_attribute(name, item.answer) for name in qualifiers)
                ),
                None,
            )
            if covering is None:
                return EvidencePlan("insufficient", (), (), "qualifier_uncovered")
            if _INSTRUCTION_INJECTION.search(covering.answer):
                return EvidencePlan("insufficient", (), (), "answer_needs_review")
            return EvidencePlan("grounded", (), (covering.answer,), "qualifier_covered")
        if is_property_question:
            # 明确在问本店、但主题落在有限清单之外：不放行模型的本店断言。
            return EvidencePlan("insufficient", (), (), "topic_unrecognized")
        if entries and _VAGUE_REFERENCE.search(question_text):
            # 指代不清且确实检索到候选：先问清楚，不让模型替客人认领某一项。
            return EvidencePlan("unclear", (), (), "intent_unconfirmed")
        return EvidencePlan("general", (), (), "not_property_question")

    chosen: list[str] = []
    for topic in topics:
        supporting = supporting_for_topic(topic, question_text, entries)
        if not supporting:
            return EvidencePlan("insufficient", topics, (), f"no_support:{topic.name}")
        attributes = asked_attributes(question_text, topic)
        # 每个主题只取第一条覆盖全部所问属性的问答：检索顺序即相关度，同一单元
        # 内条件完整；不把多条答案的数字、费用、否定拆开重组。
        covering = next(
            (
                item
                for item in supporting
                if all(_covers_attribute(attribute, item.answer) for attribute in attributes)
            ),
            None,
        )
        if covering is None:
            return EvidencePlan(
                "insufficient",
                topics,
                (),
                f"attribute_uncovered:{topic.name}",
            )
        if covering.answer not in chosen:
            chosen.append(covering.answer)

    if any(_INSTRUCTION_INJECTION.search(item) for item in chosen):
        # 需要人工复核的条目不原样转发，也不让模型改写后发出。
        return EvidencePlan("insufficient", topics, (), "answer_needs_review")
    if _conflicting_fee_claims(chosen):
        return EvidencePlan("insufficient", topics, (), "conflicting_answers")
    if len(chosen) > 1 and sum(len(item) for item in chosen) > STATIC_REPLY_MAX_CHARS:
        # 单条审核问答是最小证据单元，再长也整条发出，绝不截掉尾部的条件与例外；
        # 只有需要拼接多条时才可能超出预算，这时请客人把问题问得更具体。
        return EvidencePlan("insufficient", topics, (), "reply_budget_exceeded")
    return EvidencePlan("grounded", topics, tuple(chosen), "covered")


def compose_static_reply(answers: Sequence[str]) -> str:
    """把覆盖所问属性的审核答案按来源顺序拼成回复，不改写正文。"""
    return "\n".join(item.strip() for item in answers if item.strip())
