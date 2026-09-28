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
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from homestay_bot.services.guest_reply_policy import fits_guest_reply_parts
from homestay_bot.services.knowledge_service import (
    PropertyTopic,
    detect_property_topics,
    normalize_text,
)
from homestay_bot.services.reply_plan import ReplyEvidence, ReplyPart

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
            r"收费|费用|费每|费多少|多少钱|价格|要钱|免费|另收|加钱|押金|\d+\s*元"
            r"|how\s+much|\bfee\b|\bcost\b|\bfree\b|\bcharge",
            re.IGNORECASE,
        ),
    ),
    (
        "location",
        re.compile(
            # 「几层」「爬楼」问的是楼层位置（「401要爬几层楼」，门禁 K-电梯-C）。
            r"在哪|哪里|哪儿|放哪|位置|放在|怎么走|几楼|楼层|几层|爬楼"
            r"|\bwhere\b|which\s+floor|how\s+do\s+i\s+get",
            re.IGNORECASE,
        ),
    ),
    (
        # 办事流程：「怎么登记」「到了门口怎么进」「怎么上楼」。此前这类问法认不出属性，
        # 判成 unknown，认得出主题也拿不到固定回答（2026-09-29 测试号实测）。
        # 收费、走法、用法、故障求助另有归属，不算流程。
        "procedure",
        re.compile(
            r"怎么(?!样|办|收|算|付|走|用|开|关|调|卖)|如何(?!使用|收)|要做什么|需要做什么"
            r"|流程|步骤|手续"
            r"|how\s+(?:do|can|should)\s+(?:i|we)\s+(?!use|turn|adjust|set|get\s+to)",
            re.IGNORECASE,
        ),
    ),
    ("voltage", re.compile(r"电压|\d+\s*[vV]|多少伏")),
    ("contents", re.compile(r"(?:有什么|有哪些)\s*$|what.+(?:contain|include)\s*$", re.I)),
    ("distance", re.compile(r"多远|距离|how\s+far|distance", re.IGNORECASE)),
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
        "casting",
        re.compile(r"投屏|screen\s*(?:cast|mirror)|casting", re.I),
        re.compile(r"投屏|screen\s*(?:cast|mirror)|casting", re.I),
    ),
    (
        "radiator",
        re.compile(r"暖气|radiator", re.I),
        re.compile(r"暖气|radiator", re.I),
    ),
    (
        "kettle",
        re.compile(r"烧水壶|热水壶|\bkettle\b", re.I),
        re.compile(r"烧水壶|热水壶|\bkettle\b", re.I),
    ),
    (
        "wheelchair_stay",
        re.compile(r"轮椅.{0,12}住|坐轮椅|wheelchair.{0,12}stay", re.I),
        re.compile(r"轮椅.{0,12}(?:进|通|住)|wheelchair.{0,20}(?:fit|access|enter)", re.I),
    ),
    (
        "platform_invoice",
        re.compile(r"(?:携程|美团|平台).{0,12}(?:订|订单).*发票|发票.*(?:携程|美团|平台)", re.I),
        re.compile(r"(?:携程|美团|平台).{0,24}(?:申请|开).{0,4}发票"),
    ),
    (
        "post_checkout_storage",
        re.compile(r"退房后.{0,16}(?:行李|箱子|寄存)"),
        re.compile(r"退房当(?:天|日).{0,24}(?:前|后|寄存)|退房后.{0,16}(?:寄存|存放)"),
    ),
    (
        "spare_bedding",
        re.compile(r"备用.{0,8}(?:被子|枕头|床品)|spare\s+(?:bedding|pillows?|blankets?)", re.I),
        re.compile(r"备用.{0,8}(?:被子|枕头|床品)|spare\s+(?:bedding|pillows?|blankets?)", re.I),
    ),
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
        # 延迟退房属于额外能力；普通退房时间和退房当日寄存行李均不能证明它。
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
            r"(?:延迟|推迟|延后)退房|晚一?点退房|退房.{0,6}(?:延迟|推迟|延后)"
            r"|late\s+check-?\s?out|check-?\s?out\s+late",
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
# 流程答案要写出具体动作，只说「可以」「支持」不算回答了怎么做。
_PROCEDURE_EVIDENCE = re.compile(
    r"扫|填|登记|联系|进入|进门|刷|按|输入|提交|点击|出示|前往|乘|坐|直走|左转|右转|左边|右边"
    r"|\b(?:scan|fill|register|contact|enter|press|show|take|go|turn)\b",
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

# 未知物品的借用请求仍属于需要核实的服务能力，不能落入无约束通用回答。
_BORROWING_REQUEST = re.compile(
    r"(?:能|可以|可否|能否|想|要|有没有).{0,6}借(?!钱|款|贷)|借用"
    r"|\b(?:can|could|may)\s+(?:i|we)\s+borrow\b",
    re.IGNORECASE,
)

# 指代不清且检索到候选时澄清，普通旅游和通用问题不受影响。
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
    parts: tuple[ReplyPart, ...] = ()

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
        if scoped and len(detect_property_topics(question_text)) > 1:
            clauses = scoped
    attributes = {
        name
        for name, pattern in _ATTRIBUTE_QUESTION_PATTERNS
        for clause in clauses
        if pattern.search(clause)
    }
    if (
        topic is not None
        and topic.name == "网络"
        and re.search(r"密码|蜜码|password", " ".join(clauses), re.I)
    ):
        attributes.add("network_password")
    if topic is not None and topic.name == "加床":
        attributes.update(
            f"room:{room}" for room in re.findall(r"(?<!\d)\d{3}(?!\d)", question_text)
        )
    attributes.update(
        f"special:{name}"
        for name, question_pattern, _ in _SPECIAL_QUALIFIERS
        for clause in [" ".join(clauses)]
        if question_pattern.search(clause)
    )
    # 只对去除主题后剩下的明确存在性问法放行；未知限定必须保守缺失。
    if attributes:
        return attributes
    if any(
        re.search(r"完整(?:规则|政策)|全部(?:规定|规则)|all (?:rules|policies)", clause, re.I)
        for clause in clauses
    ):
        return {"existence"}
    remainder = " ".join(clauses)
    detected_topics = detect_property_topics(remainder)
    for detected in detected_topics:
        remainder = detected.aliases.sub("", remainder)
    # 仅去除普通政策问法的连接词，不剥掉身高、饮食等事实限定。
    if any(item.name == "宠物" for item in detected_topics):
        remainder = re.sub(r"携带|带|入住", "", remainder)
    if any(item.name == "行李寄存" for item in detected_topics):
        remainder = re.sub(r"行李", "", remainder)
    remainder = re.sub(
        r"房间里|房费|又|开|另外|同时|以及|你们|民宿|房间|请问|是否|有没有|有无|提供|我家|一起住|附近|包|店|有|吗|呢|呀|的|可以|能|不|使用|用"
        r"|do you (?:have|serve|provide)|is there|are there|available|the|a|an|\W",
        "",
        remainder,
        flags=re.IGNORECASE,
    )
    return {"existence"} if not remainder.strip() else {"unknown"}


def _covers_attribute(attribute: str, answer: str) -> bool:
    """判断一条审核答案是否覆盖某个属性。"""
    if attribute == "existence":
        return True
    if attribute == "network_password":
        return re.search(r"密码|password", answer, re.I) is not None
    if attribute == "voltage":
        return re.search(r"\d+\s*(?:V(?![a-z])|伏|volts?\b)", answer, re.I) is not None
    if attribute == "contents":
        return re.search(r"有|配备|配有|includes?|contains?|equipped", answer, re.I) is not None
    if attribute == "distance":
        return (
            re.search(r"\d+\s*(?:米|公里|分钟|minute|meter|km)|步行|walk", answer, re.IGNORECASE)
            is not None
        )
    if attribute == "time":
        return _TIME_EVIDENCE.search(answer) is not None
    if attribute == "location":
        return _LOCATION_EVIDENCE.search(answer) is not None
    if attribute == "operation":
        return _OPERATION_EVIDENCE.search(answer) is not None
    if attribute == "procedure":
        return _PROCEDURE_EVIDENCE.search(answer) is not None
    if attribute == "fee":
        # 再检查当前主题正文，避免调用方漏认口语费用问法后用位置资料作证。
        return (
            re.search(
                r"免费|收费|费用|加收|\d+\s*(?:元|yuan|CNY)|\b(?:free|fee|cost|charge)",
                answer,
                re.I,
            )
            is not None
        )
    if attribute.startswith("room:"):
        # 正文点名房号，或明确给出封闭允许清单及其他房间禁用规则，才覆盖房号限定。
        room = attribute.split(":", 1)[1]
        return re.search(rf"(?<!\d){re.escape(room)}(?!\d)", answer) is not None or (
            re.search(r"只有.{0,50}可以加床", answer) is not None
            and re.search(r"其他房间.{0,20}不能加床", answer) is not None
        )
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
    # 先移除明确的免费/不收费表达再找收费，避免“不收费”自己和自己冲突。
    free = any(
        _AFFIRMATIVE_FEE_FREE.search(re.sub(r"不免费|并非免费|not\s+free", "", item))
        for item in answers
    )
    paid = any(_PAID_FEE.search(_AFFIRMATIVE_FEE_FREE.sub("", item)) for item in answers)
    return free and paid


def _topic_attribute_text(topic: PropertyTopic, answer: str) -> str:
    """属性只取当前主题的分句；无主语句承接最近明确主题，不借用他项数值。

    ponytail: 只处理显式主题和紧邻承接，不证明任意代词或复杂并列的语义归属。
    """
    active = {topic.name}
    passages = []
    for passage in re.split(r"[，,。！？!?；;\n]+|(?<=\.)\s+", answer):
        named = {item.name for item in detect_property_topics(passage)}
        # 早餐「在公共客厅用餐」是在补充用餐地点，不会把紧随其后的每位价格改成客厅费。
        # 公共客厅自己的开放或收费政策仍切换主题，不能拿来证明早餐价格。
        dining_location = (
            active == {"早餐"}
            and named == {"公共区域"}
            and re.search(r"用餐|就餐|\bserved\b", passage, re.I) is not None
        )
        if named and not dining_location:
            active = named
        if topic.name in active:
            passages.append(passage)
    return "\n".join(passages)


def _attribute_evidence_text(
    topic: PropertyTopic, question: str, attribute: str, answer: str
) -> str:
    """普通入住和退房钟点只比较对应政策，不混入提前、延迟或发密码时刻。"""
    # 加床正文中的「含一套床品」不是政策切换；封闭房号清单与「其他房间」要一起核验。
    if topic.name == "加床" and attribute.startswith("room:"):
        return answer
    text = _topic_attribute_text(topic, answer)
    if topic.name != "入住退房时间" or attribute != "time":
        return text
    if asked_attributes(question, topic) & {"special:early_checkin", "special:late_checkout"}:
        return text
    directions = []
    if re.search(r"入住|check[ -]?in", question, re.I):
        directions.append(
            r"入住(?:时间)?(?:为|是|[:：])|(?:\d|点|时).{0,12}入住|check[ -]?in\s+(?:is|time)"
        )
    if re.search(r"退房|check[ -]?out", question, re.I):
        directions.append(
            r"退房(?:时间)?(?:为|是|[:：])|(?:\d|点|时).{0,12}退房|check[ -]?out\s+(?:is|time)"
        )
    passages = []
    special_policy = False
    for passage in text.splitlines():
        # 附加政策的后句可能省略主语；持续保留其归属，不能借后句钟点证明普通安排。
        # 前面已经确认的普通钟点不受后面「不支持提前入住」等限制影响。
        if re.search(
            r"提前入住|延迟退房|early\s+check|late\s+check|check.{0,8}(?:early|late)"
            r"|密码|door\s+code",
            passage,
            re.I,
        ):
            special_policy = True
        if not special_policy and any(re.search(pattern, passage, re.I) for pattern in directions):
            passages.append(passage)
    return "\n".join(passages)


def already_clarified(messages: Sequence[dict[str, str]]) -> bool:
    """会话里是否已经发出过澄清提问。"""
    return any(
        item.get("role") == "assistant"
        and any(marker in str(item.get("content", "")) for marker in _CLARIFY_MARKERS)
        for item in messages
    )


def _knowledge_part(question: str, entry: Any, *, text: str | None = None) -> ReplyPart:
    """审核答案原文和来源绑定为不可拆散的事实单元；房源卡片只取与话题相关的行。"""
    answer = entry.answer if text is None else text
    return ReplyPart(
        question=question,
        status="grounded",
        text=answer,
        evidence=(
            ReplyEvidence(
                source_kind="knowledge",
                source_id=str(getattr(entry, "source_id", getattr(entry, "id", ""))),
                property_id=getattr(entry, "property_id", None),
                fetched_at=datetime.now(UTC),
                conditions=(answer,),
            ),
        ),
    )


def _bigrams(text: str) -> set[str]:
    """归一化后按相邻两字切分，用于比较两句问法的重合程度。"""
    compact = re.sub(r"[\s\W_]+", "", normalize_text(text))
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def _is_property_card(entry: Any) -> bool:
    """房源卡片以负的房源编号作来源编号（knowledge_service.property_card_snippet）。"""
    source_id = getattr(entry, "source_id", None)
    return isinstance(source_id, int) and source_id < 0


def _answer_for_topic(topic: PropertyTopic, entry: Any) -> str:
    """固定回答的正文：知识条目用原文；房源卡片只取点名该话题的行，不整张发出。

    卡片每行一项（「停车：……」「地址与楼层：……」），按行过滤不会切断同一项内的
    条件；一行都没点名时退回原文，交由证据判定照常处理。
    """
    if not _is_property_card(entry):
        return str(entry.answer)
    lines = [
        line
        for line in str(entry.answer).split("\n")
        if topic.aliases.search(normalize_text(line))
    ]
    return "\n".join(lines) or str(entry.answer)


# 追问判定（Spec F3，借鉴世界书的扫描深度）：去掉指代、语气和常见属性问法后，
# 剩下的实义字不超过 2 个，才算是接着上文在问。「黄鹤楼在哪」剩下「黄鹤楼」，
# 是新问题，不借上文话题。
_FOLLOW_UP_FILLERS = re.compile(
    r"那个|这个|那|这|它|他|她|个|也|还|又|再|要|能|可以|可不可以|吗|呢|吧|啊|呀|哦|的|是|有没有|有|"
    r"多少|几点|几|在哪里|在哪儿|在哪|哪里|哪儿|怎么|如何|收费|费用|免费|钱|价格|多久|时间|"
    r"\b(?:is|it|that|this|there|how|much|what|time|where|when|can|could|i|we|do|does|"
    r"you|the|a|an|cost|free|price|any|also|too)\b|[\s\W_]+",
    re.IGNORECASE,
)
# 单纯的回应不是追问：「ok」「好的」不能把上一轮的早餐话题再答一遍（回归 MT-ok语言）。
_ACKNOWLEDGEMENT = re.compile(
    r"^(?:ok|okay|好|好的|好滴|嗯|嗯嗯|行|可以|收到|谢谢|多谢|thanks?|thank you|知道了|明白了)"
    r"[\s。.!！~～]*$",
    re.IGNORECASE,
)
_FOLLOW_UP_MAX_CHARS = 20


def carry_followup_topic(question: str, earlier_guest_messages: Sequence[str]) -> str:
    """最后一句认不出本店话题、又像追问时，借用这位客人更早消息里最近的话题。

    用户决定（2026-09-28）：往前看这位客人在本会话里的所有消息。话题识别在本地
    完成，不增加模型 token。只借话题：返回「话题词：原问题」，问什么属性（时间、
    费用、位置）仍由原问题决定，例如「早餐几点？」之后问「那要钱吗」，答的是早餐
    的费用。认不出、不像追问或找不到上文话题时原样返回。
    """
    text = question.strip()
    if (
        not text
        or len(text) > _FOLLOW_UP_MAX_CHARS
        or _ACKNOWLEDGEMENT.match(text)
        or detect_property_topics(text)
        # 去掉虚词后什么都不剩才算追问（「那要钱吗」「几点」）。此前留 2 个字的余量，
        # 「怎么登记」剩下「登记」被当成追问，借用上文的停车话题，回了「尚未确认
        # 停车信息」（2026-09-29 测试号实测）。
        or _FOLLOW_UP_FILLERS.sub("", text)
    ):
        return question
    for earlier in reversed(earlier_guest_messages):
        normalized = normalize_text(earlier)
        for topic in detect_property_topics(earlier):
            match = topic.aliases.search(normalized)
            word = match.group(0) if match else topic.name
            return f"{word}：{text}"
    return question


def build_evidence_plan(
    question_text: str,
    knowledge: Sequence[Any],
    *,
    supporting_for_topic: Callable[[PropertyTopic, str, list[Any]], list[Any]],
    is_property_question: bool,
    target_date: date | None = None,
    target_end_date: date | None = None,
) -> EvidencePlan:
    """按知识生效边界分段核验，不让跨期入住沿用单日答案。"""
    if target_date is None:
        return _build_evidence_plan_for_period(
            question_text,
            knowledge,
            supporting_for_topic=supporting_for_topic,
            is_property_question=is_property_question,
        )
    end = target_end_date or target_date
    boundaries = {target_date, end + timedelta(days=1)}
    for entry in knowledge:
        start_on, end_on = getattr(entry, "valid_from", None), getattr(entry, "valid_until", None)
        if start_on and target_date < start_on <= end:
            boundaries.add(start_on)
        if end_on and target_date <= end_on < end:
            boundaries.add(end_on + timedelta(days=1))
    ordered = sorted(boundaries)
    plans = []
    all_parts = []
    for start, stop in zip(ordered, ordered[1:], strict=False):
        entries = [
            entry
            for entry in knowledge
            if (getattr(entry, "valid_from", None) is None or entry.valid_from <= start)
            and (
                getattr(entry, "valid_until", None) is None
                or entry.valid_until >= stop - timedelta(days=1)
            )
        ]
        plan = _build_evidence_plan_for_period(
            question_text,
            entries,
            supporting_for_topic=supporting_for_topic,
            is_property_question=is_property_question,
        )
        plans.append(plan)
        for part in plan.parts:
            text = part.text
            if len(ordered) > 2 and text:
                text = f"{start.isoformat()} — {(stop - timedelta(days=1)).isoformat()}：{text}"
            all_parts.append(
                part.model_copy(
                    update={
                        "text": text,
                        "evidence": tuple(
                            evidence.model_copy(
                                update={
                                    "target_date": start,
                                    "target_end_date": stop - timedelta(days=1),
                                }
                            )
                            for evidence in part.evidence
                        ),
                    }
                )
            )
    first = plans[0]
    return EvidencePlan(
        "insufficient" if any(plan.status == "insufficient" for plan in plans) else first.status,
        first.topics,
        tuple(part.text for part in all_parts if part.status == "grounded"),
        first.reason,
        tuple(all_parts),
    )


def _build_evidence_plan_for_period(
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
        if _BORROWING_REQUEST.search(question_text):
            return EvidencePlan("insufficient", (), (), "borrowing_unconfirmed")
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
            return EvidencePlan(
                "grounded",
                (),
                (covering.answer,),
                "qualifier_covered",
                (_knowledge_part(question_text, covering),),
            )
        if is_property_question:
            # 明确在问本店、但主题落在有限清单之外：不放行模型的本店断言。
            return EvidencePlan("insufficient", (), (), "topic_unrecognized")
        if entries and _VAGUE_REFERENCE.search(question_text):
            # 指代不清且确实检索到候选：先问清楚，不让模型替客人认领某一项。
            return EvidencePlan("unclear", (), (), "intent_unconfirmed")
        return EvidencePlan("general", (), (), "not_property_question")

    chosen: list[str] = []
    parts: list[ReplyPart] = []
    for topic in topics:
        supporting = supporting_for_topic(topic, question_text, entries)
        attributes = asked_attributes(question_text, topic)
        for attribute in sorted(attributes):
            covering_items = [
                item
                for item in supporting
                if _covers_attribute(
                    attribute,
                    _attribute_evidence_text(topic, question_text, attribute, item.answer),
                )
                and not _INSTRUCTION_INJECTION.search(item.answer)
            ]
            # 房间专属覆盖只作用于同一属性，不能吞掉通用条目的互补位置等事实。
            specific = [
                item for item in covering_items if getattr(item, "scope", None) == "property"
            ]
            covering_items = specific or covering_items
            # 房源卡片只作兜底：同一属性有专门知识条目时不参与，既不抢固定回答，也不
            # 因为写法不同（「3元/小时」与「3元/小时，每日封顶40元」）被判成收费冲突。
            # 2026-09-29 测试号实测：卡片排在首位被选中，问停车回了整张卡片。
            dedicated = [item for item in covering_items if not _is_property_card(item)]
            covering_items = dedicated or covering_items
            # 多条都能作答时，问法与客人问题重合最多的条目优先：「门禁卡在哪里」的答案
            # 顺带提到停车场，不能抢在「开车来停哪里」前面回答停车问题。排序稳定，
            # 重合相同时保持检索给出的相关度顺序。
            # 重合相同时，问法本身点名这个主题的条目优先：进门条目的答案写了「登记表」，
            # 不能和入住实名登记条目打平后靠检索顺序碰运气（2026-09-29）。只作次序，
            # 「开车来停哪里」这种问法认不出别名，不能因此排到收费条目后面。
            asked = _bigrams(question_text)
            covering_items.sort(
                key=lambda item: (
                    -len(asked & _bigrams(str(getattr(item, "question", "")))),
                    not topic.aliases.search(normalize_text(str(getattr(item, "question", "")))),
                )
            )
            evidence_texts = [
                _attribute_evidence_text(topic, question_text, attribute, item.answer)
                for item in covering_items
            ]
            # 「免费还是收费」互相矛盾只对费用问题有意义。问位置时若也检查，一条
            # 「30分钟内免费、超过按2元/小时」的收费条目会被判成自相矛盾，连带把
            # 停车位置也判为未确认（春和景明知识导入后「开车停哪里」回「尚未确认」）。
            conflict = attribute == "fee" and _conflicting_fee_claims(evidence_texts)
            times = {
                tuple(_TIME_EVIDENCE.findall(text))
                for text in evidence_texts
                if _TIME_EVIDENCE.search(text)
            }
            conflict = conflict or (attribute == "time" and len(times) > 1)
            fees = {
                tuple(re.findall(r"\d+(?:\.\d+)?\s*(?:元|yuan|CNY)", text))
                for text in evidence_texts
                if re.search(r"\d+\s*(?:元|yuan|CNY)", text)
            }
            conflict = conflict or (attribute == "fee" and len(fees) > 1)
            covering = covering_items[0] if covering_items and not conflict else None
            label = {"time": "时间", "fee": "费用", "location": "位置", "procedure": "流程"}.get(
                attribute, ""
            )
            if covering is None:
                parts.append(ReplyPart(question=topic.name + label, status="missing", text=""))
                continue
            answer_text = _answer_for_topic(topic, covering)
            if answer_text not in chosen:
                chosen.append(answer_text)
                parts.append(_knowledge_part(topic.name, covering, text=answer_text))

    if any(_INSTRUCTION_INJECTION.search(item) for item in chosen):
        # 需要人工复核的条目不原样转发，也不让模型改写后发出。
        return EvidencePlan("insufficient", topics, (), "answer_needs_review")
    if not fits_guest_reply_parts(compose_static_reply(chosen)):
        # 审核原文绝不截断条件与例外：超长时拆成多条发送，拆到段数上限仍放不下才
        # 请客人细化，并提示管理员缩短这条知识。
        return EvidencePlan("insufficient", topics, (), "reply_exceeds_message_limit")
    if len(chosen) > 1 and sum(len(item) for item in chosen) > STATIC_REPLY_MAX_CHARS:
        # 单条审核问答是最小证据单元，再长也整条发出，绝不截掉尾部的条件与例外；
        # 只有需要拼接多条时才可能超出预算，这时请客人把问题问得更具体。
        return EvidencePlan("insufficient", topics, (), "reply_budget_exceeded")
    missing = any(part.status == "missing" for part in parts)
    return EvidencePlan(
        "insufficient" if missing else "grounded",
        topics,
        tuple(chosen),
        "partial" if missing else "covered",
        tuple(parts),
    )


def compose_static_reply(answers: Sequence[str]) -> str:
    """把覆盖所问属性的审核答案按来源顺序拼成回复，不改写正文。"""
    return "\n".join(item.strip() for item in answers if item.strip())
