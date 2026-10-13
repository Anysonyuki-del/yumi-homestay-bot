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

# 缺口分项的属性名后缀，供未确认文案识别「早餐时间」「停车费用」等具体缺口。
_ATTRIBUTE_LABELS = {"time": "时间", "fee": "费用", "location": "位置", "procedure": "流程"}

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
            # 要地址本身就是问位置（「地址发我一下」「地址是什么」）。
            r"|地址|定位"
            r"|\bwhere\b|which\s+floor|how\s+do\s+i\s+get|\baddress\b",
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
            # 是非问法「保安要登记吗」「需要实名登记吗」也是在问要办的手续。
            r"|(?:需要|要|得)先?(?:登记|实名|办理|出示|刷卡|预约)"
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


def _rank_covering(item: Any, topic: PropertyTopic, asked: set[str]) -> int:
    """条目问法与客人问题越贴近、越直接点名本主题，排序分越高。"""
    item_question = str(getattr(item, "question", ""))
    names_topic = topic.aliases.search(normalize_text(item_question)) is not None
    return len(asked & _bigrams(item_question)) + (2 if names_topic else 0)


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
    object_scoped: bool = False,
) -> EvidencePlan:
    """按知识生效边界分段核验，不让跨期入住沿用单日答案。

    `object_scoped` 为真时，冲突与特殊时期优先只在同一对象内进行，并执行 D-G8 保守否决
    （修复 Spec §2.3(3)）；只在计划成功的逐项回退时开启，与证据选择核验同一口径。
    计划失败的整句路径保持现状（已声明边界）。
    """
    if target_date is None:
        return _build_evidence_plan_for_period(
            question_text,
            knowledge,
            supporting_for_topic=supporting_for_topic,
            is_property_question=is_property_question,
            object_scoped=object_scoped,
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
    segmented = len(ordered) > 2
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
            object_scoped=object_scoped,
        )
        plans.append(plan)
        last = stop - timedelta(days=1)
        label = segment_date_label(start, last) if segmented else ""
        period_parts = list(plan.parts)
        if segmented and plan.status == "insufficient" and not period_parts:
            # 复核或预算阻断的段没有分项：用原问题补一个缺失分项，最终正文仍能说明哪天未确认。
            period_parts = [ReplyPart(question=question_text, status="missing", text="")]
        for part in period_parts:
            update: dict[str, Any] = {
                "evidence": tuple(
                    evidence.model_copy(update={"target_date": start, "target_end_date": last})
                    for evidence in part.evidence
                )
            }
            if label and part.text:
                update["text"] = label + part.text
            if label and part.status == "missing":
                # 缺失分项正文由回复层按主题生成，日期前缀先放在问题上，回复层剥离后再加回正文。
                update["question"] = label + part.question
            all_parts.append(part.model_copy(update=update))
    first = plans[0]
    answers = tuple(part.text for part in all_parts if part.status == "grounded")
    budget = ""
    if segmented and answers:
        # 各段单独都在预算内，拼起来仍可能超限；超限时关闭整个合并答案，不截断条件。
        if not fits_guest_reply_parts(compose_static_reply(list(answers))):
            budget = "reply_exceeds_message_limit"
        elif len(answers) > 1 and sum(len(item) for item in answers) > STATIC_REPLY_MAX_CHARS:
            budget = "reply_budget_exceeded"
    if budget:
        all_parts = [
            part.model_copy(update={
                "status": "missing",
                "text": "",
                "question": _segment_label_of(part) + part.question,
                # 未发出的答案不能再挂来源，否则诊断页会显示一个没用上的事实来源。
                "evidence": (),
            })
            if part.status == "grounded" else part
            for part in all_parts
        ]
        answers = ()
    insufficient = budget or any(plan.status == "insufficient" for plan in plans)
    return EvidencePlan(
        "insufficient" if insufficient else first.status,
        first.topics,
        answers,
        _combined_reason(plans, budget),
        tuple(all_parts),
    )


# 分段阻断原因的代表优先级（周末 Spec §2.3）：只决定记录哪一个原因，不降低其他阻断效力。
_BLOCKING_RANKS = (
    ("answer_needs_review", "borrowing_unconfirmed"),
    ("conflict",),
    ("period_veto",),
    ("reply_exceeds_message_limit", "reply_budget_exceeded"),
)


def _blocking_rank(reason: str) -> int | None:
    """返回阻断原因的优先级（越小越优先）；普通缺失或成功原因返回空。"""
    for rank, names in enumerate(_BLOCKING_RANKS):
        # 冲突原因带后缀（conflict_fee 等），按前缀归类。
        if reason in names or (names == ("conflict",) and reason.startswith("conflict")):
            return rank
    return None


def _combined_reason(plans: Sequence[EvidencePlan], budget: str) -> str:
    """汇总所有分段的原因，不让第一段的成功或普通缺失遮住后面段的冲突、复核与预算阻断。

    有阻断时按优先级取代表原因，同级取日期最早的段；没有阻断时取第一个不足段的原因；
    全部可答才保留第一段的成功原因。
    """
    reasons = [plan.reason for plan in plans] + ([budget] if budget else [])
    blocking = [
        (rank, index, reason)
        for index, reason in enumerate(reasons)
        if (rank := _blocking_rank(reason)) is not None
    ]
    if blocking:
        return min(blocking)[2]
    insufficient = next((plan.reason for plan in plans if plan.status == "insufficient"), None)
    return insufficient or plans[0].reason


def segment_date_label(start: date, end: date) -> str:
    """分段日期前缀（D-W6）：单日写 `YYYY-MM-DD：`，多日写 `YYYY-MM-DD — YYYY-MM-DD：`。

    年份完整、不分语言，中英文回复共用；日期只来自本地冻结的段边界。
    """
    if start == end:
        return f"{start.isoformat()}："
    return f"{start.isoformat()} — {end.isoformat()}："


_SEGMENT_LABEL = re.compile(r"^\d{4}-\d{2}-\d{2}(?: — \d{4}-\d{2}-\d{2})?：")


def split_segment_label(text: str) -> tuple[str, str]:
    """拆出 `segment_date_label` 生成的前缀，返回（前缀, 其余）；没有前缀时前缀为空。

    回复层先按其余部分做主题与凭据判断，再把前缀加回正文，日期标注不改变安全状态。
    """
    match = _SEGMENT_LABEL.match(text)
    return (match[0], text[match.end():]) if match else ("", text)


def _segment_label_of(part: ReplyPart) -> str:
    """取已答分项正文上的日期前缀，用于把它改成缺失分项时保留日期。"""
    return split_segment_label(part.text)[0]


def _build_evidence_plan_for_period(
    question_text: str,
    knowledge: Sequence[Any],
    *,
    supporting_for_topic: Callable[[PropertyTopic, str, list[Any]], list[Any]],
    is_property_question: bool,
    object_scoped: bool = False,
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
    # 按对象约束时记录第一个冲突或期间否决：缺口原因不是「资料缺失」，不可转交。
    blocked = ""
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
            # 排序分 = 问法与客人问题的相邻两字重合数 + 问法本身点名这个主题时加 2。
            # 只看重合时，「怎么」「登记吗」这类虚字重合会让顺带提到主题的条目抢答：
            # 「需要实名登记吗」选成了进门条目（答案写了登记表），「怎么上楼」选成了
            # 停车条目（2026-09-29）。只看点名又不行：「开车来停哪里」认不出停车别名，
            # 会输给「停车收费吗」。加分不超过一处实词重合的量级，排序稳定。
            asked = _bigrams(question_text)

            covering_items.sort(key=lambda item: -_rank_covering(item, topic, asked))
            if object_scoped:
                covering, outcome = _object_scoped_covering(
                    question_text, topic, attribute, covering_items
                )
                label = _ATTRIBUTE_LABELS.get(attribute, "")
                if covering is None:
                    if outcome in {"conflict", "period_veto"}:
                        blocked = blocked or outcome
                    parts.append(ReplyPart(question=topic.name + label, status="missing", text=""))
                    continue
                answer_text = _answer_for_topic(topic, covering)
                if answer_text not in chosen:
                    chosen.append(answer_text)
                    parts.append(_knowledge_part(topic.name, covering, text=answer_text))
                continue
            evidence_texts = [
                _attribute_evidence_text(topic, question_text, attribute, item.answer)
                for item in covering_items
            ]
            # 「免费还是收费」互相矛盾只对费用问题有意义。问位置时若也检查，一条
            # 「30分钟内免费、超过按2元/小时」的收费条目会被判成自相矛盾，连带把
            # 停车位置也判为未确认（春和景明知识导入后「开车停哪里」回「尚未确认」）。
            conflict = _attribute_conflict(attribute, evidence_texts)
            periodic = [item for item in covering_items if _has_validity(item)]
            if conflict and periodic and len(periodic) < len(covering_items):
                # D6：同主题同属性的特殊时期政策与平时政策矛盾时，以特殊时期为准。本函数按
                # 生效边界分段调用，段内有有效期的条目覆盖整段；不矛盾时两者照常并存。
                covering_items = periodic
                evidence_texts = [
                    _attribute_evidence_text(topic, question_text, attribute, item.answer)
                    for item in covering_items
                ]
                conflict = _attribute_conflict(attribute, evidence_texts)
            covering = covering_items[0] if covering_items and not conflict else None
            label = _ATTRIBUTE_LABELS.get(attribute, "")
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
        (blocked or "partial") if missing else "covered",
        tuple(parts),
    )


def _object_scoped_covering(
    question_text: str, topic: PropertyTopic, attribute: str, covering_items: Sequence[Any]
) -> tuple[Any | None, str]:
    """按对象约束为一个主题属性选出作答条目（修复 Spec §2.3(3)）。

    返回（条目, 原因）：作答时原因为 covered；没有覆盖条目为 uncovered；同一对象内矛盾为
    conflict；D-G8 否决为 period_veto。原因供调用方区分「资料缺失」与其他缺口。

    `covering_items` 已按相关度排序，且只含本生效时段有效的条目。取排名第一的条目，只与
    同一对象的条目比较冲突；同一对象内特殊时期与平时矛盾时以特殊时期为准（D6）；同主题、
    不同对象的期间条目与答案矛盾时保守否决（D-G8）。不同对象的普通条目不互相判冲突。
    """
    if not covering_items:
        return None, "uncovered"
    top = covering_items[0]
    group = [top, *(item for item in covering_items[1:] if same_object(item, top))]
    attributes = {attribute} & {"time", "fee"}
    conflict = _object_conflict(question_text, (topic,), attributes, group)
    periodic = [item for item in group if _has_validity(item)]
    if conflict and periodic and len(periodic) < len(group):
        group = periodic
        conflict = _object_conflict(question_text, (topic,), attributes, group)
    if conflict:
        return None, "conflict"
    chosen = group[0]
    if _period_veto(question_text, (topic,), attributes, [chosen], covering_items):
        return None, "period_veto"
    return chosen, "covered"


def _has_validity(entry: Any) -> bool:
    """条目是否带有效期（特殊时期政策）。"""
    return (
        getattr(entry, "valid_from", None) is not None
        or getattr(entry, "valid_until", None) is not None
    )


@dataclass(frozen=True)
class SelectionVerdict:
    """模型证据选择的本地核验结论。

    - grounded：选中条目通过核验，`parts` 为审核原文分项；
    - missing：模型选「无」、冲突、待复核或超长，该项回未确认；
    - invalid：越界编号或跨越政策生效边界，该项回到现行证据计划。
    """

    status: Literal["grounded", "missing", "invalid"]
    reason: str
    parts: tuple[ReplyPart, ...] = ()


# 同一对象判定只删除的纯疑问与语气形式（修复 Spec §2.3(1)，D-G1）。只收不改变所指对象的
# 形式词：否定、收费限定、数量、地点、楼层、指代一律保留，「免费健身房」与「收费健身房」、
# 「一楼早餐厅」与「二楼早餐厅」因此不会归一成同一件事。长短语排在短短语之前，避免
# 「几点钟」只删掉「几点」。不复用追问填充词表 `_FOLLOW_UP_FILLERS`：它会删实义词。
_OBJECT_FORM_PHRASES = tuple(
    sorted(
        (
            "什么时候", "何时", "几点钟", "几点", "多久", "多少钱", "多少", "哪里", "在哪儿",
            "在哪", "怎么", "如何", "是否", "能否", "可不可以", "可以", "有没有",
            "吗", "呢", "吧", "啊", "呀", "请问",
        ),
        key=len,
        reverse=True,
    )
)
_OBJECT_FORM_EN = re.compile(
    r"\b(?:what\s+time|how\s+much|how\s+long|when|where|can|could|is|are|do|does|please)\b",
    re.IGNORECASE,
)
_OBJECT_PUNCTUATION = re.compile(r"[\W_]+")


def object_question_key(question: str) -> str:
    """知识标准问题的「同一对象」归一化：去掉纯疑问、语气、标点与空白后的剩余文本。

    只用于合组判定，不改变客人原问或任何分类输入。结果为空时调用方不得据此认定同一对象。
    """
    value = _OBJECT_FORM_EN.sub(" ", normalize_text(question))
    for phrase in _OBJECT_FORM_PHRASES:
        value = value.replace(phrase, "")
    return _OBJECT_PUNCTUATION.sub("", value)


def same_object(first: Any, second: Any) -> bool:
    """两条审核知识是否为同一对象（修复 Spec §2.3(2)，D-G5 修订）。

    唯一判据：适用范围与房源一致，且归一化后的标准问题非空且完全相同。同主题、问法互相
    包含、有效期都不单独证明同一对象（一楼、二楼早餐厅同主题不同对象；国庆期间早餐与
    平时早餐问法不同时也不是同一对象，见 D-G8）。

    ponytail: 只认字面同问法。同对象但措辞不同（「几点开门」与「开放时间」）会被当成
    不同对象，矛盾可能漏检，包括两条同时被模型选中时；要识别需另行确认主体关系依据，
    不在这里补楼层、套餐等词表。
    """
    if getattr(first, "scope", None) != getattr(second, "scope", None):
        return False
    if getattr(first, "property_id", None) != getattr(second, "property_id", None):
        return False
    left = object_question_key(str(getattr(first, "question", "")))
    return bool(left) and left == object_question_key(str(getattr(second, "question", "")))


def _valid_during(entry: Any, start: date, end: date) -> bool:
    """条目有效期与目标区间是否相交；无有效期的条目始终有效。"""
    valid_from = getattr(entry, "valid_from", None)
    valid_until = getattr(entry, "valid_until", None)
    return (valid_from is None or valid_from <= end) and (
        valid_until is None or valid_until >= start
    )


def _crosses_validity_boundary(entry: Any, start: date, end: date) -> bool:
    """条目有效期的起止落在目标区间内部时，单条答案不能覆盖整段住宿。"""
    valid_from = getattr(entry, "valid_from", None)
    valid_until = getattr(entry, "valid_until", None)
    return bool(
        (valid_from is not None and start < valid_from <= end)
        or (valid_until is not None and start <= valid_until < end)
    )


def _attribute_conflict(attribute: str, texts: Sequence[str]) -> bool:
    """沿用证据计划的冲突口径：收费免费互斥、金额集合不一致、钟点集合不一致。"""
    if attribute == "fee":
        covering = [text for text in texts if _covers_attribute("fee", text)]
        fees = {
            tuple(re.findall(r"\d+(?:\.\d+)?\s*(?:元|yuan|CNY)", text))
            for text in covering
            if re.search(r"\d+\s*(?:元|yuan|CNY)", text)
        }
        return _conflicting_fee_claims(covering) or len(fees) > 1
    if attribute == "time":
        times = {
            tuple(_TIME_EVIDENCE.findall(text)) for text in texts if _TIME_EVIDENCE.search(text)
        }
        return len(times) > 1
    return False


def _object_conflict(
    question: str,
    topics: Sequence[PropertyTopic],
    attributes: set[str],
    entries: Sequence[Any],
) -> str | None:
    """返回一组条目在客人问到的钟点或收费上第一个出现矛盾的属性，没有矛盾返回空。

    主题认得出时只比较该主题该属性的分句，与证据计划同一口径。选择核验与逐项回退共用。
    """
    for attribute in sorted(attributes):
        if topics:
            texts = [
                _attribute_evidence_text(topic, question, attribute, entry.answer)
                for topic in topics
                for entry in entries
            ]
        else:
            texts = [entry.answer for entry in entries]
        if _attribute_conflict(attribute, [text for text in texts if text]):
            return attribute
    return None


def _period_veto(
    question: str,
    topics: Sequence[PropertyTopic],
    attributes: set[str],
    answers: Sequence[Any],
    candidates: Sequence[Any],
) -> bool:
    """D-G8：同主题、无法确认同一对象、在目标日期有效的特殊时期条目与答案矛盾时否决。

    `candidates` 必须已按目标日期过滤（只含有效期覆盖目标日期的条目）。命中时既不用期间
    条目替换答案，也不发出可能已被它覆盖的平时答案，该项回未确认。

    ponytail: 保守否决。只看所问属性（钟点或收费）是否矛盾，无法区分楼层、餐厅或「开放与
    领取」两种时间，同主题、在期间有效但与本题无关的政策（二楼早餐厅、打包早餐）也会拦下
    平时答案；要消除这类误拦需另行确认主体关系依据。员工把特殊时期条目的标准问题写成与
    平时条目相同，即属同一对象，按 D6 发出期间答案。
    """
    names = {topic.name for topic in topics}
    if not names or not attributes:
        return False
    for other in candidates:
        if not _has_validity(other) or any(other is answer for answer in answers):
            continue
        if any(same_object(other, answer) for answer in answers):
            continue
        other_topics = {
            item.name for item in detect_property_topics(str(getattr(other, "question", "")))
        }
        if not names & other_topics:
            continue
        for answer in answers:
            if _object_conflict(question, topics, attributes, [answer, other]):
                return True
    return False


def verify_selected_evidence(
    question: str,
    answer_ids: Sequence[int],
    related_ids: Sequence[int],
    candidates: Sequence[Any],
    *,
    target_date: date | None = None,
    target_end_date: date | None = None,
) -> SelectionVerdict:
    """确定性核验主调用为一个子问题选择的证据（Spec §2.4）。

    1. 全部编号必须属于本轮合法候选，否则该项回到现行证据计划；
    2. 冲突检查只在同一对象内进行（`same_object`）：每个回答条目与同一对象的合法候选比较
       客人问到的钟点或收费；related 标签不决定分组，只参与越界核验；
    3. 期间政策优先（D6）只在同一对象内生效；同主题但无法确认同一对象的期间条目与答案
       矛盾时按 D-G8 保守否决（`_period_veto`）；跨越生效边界交回现行计划分段；
    4. 保留指令注入与长度规则；核验通过的条目整条发出，不截断、不改写。
    查不出冲突不等于证明没有冲突。

    ponytail: 冲突只按收费、金额与钟点三类规则识别，其他属性的矛盾查不出；新主题依赖
    模型标出的相关集。扩充冲突规则前，不能把本函数当作语义一致性的证明。
    """
    by_id = {
        int(getattr(entry, "source_id", 0)): entry
        for entry in candidates
        if isinstance(getattr(entry, "source_id", None), int)
    }
    if any(item not in by_id for item in answer_ids):
        return SelectionVerdict("invalid", "out_of_candidates")
    # 相关编号只是模型的提示，不决定分组：逐项取证后本项合法候选只来自本项检索，信封里
    # 却仍能看到别项的条目，模型标它为相关是正常的。忽略本项候选之外的相关编号，不让它
    # 否决本项合法的回答（1.70.0 候选门禁 K-访客：答 9033、相关 9032 被整条判越界）。
    related_ids = [item for item in related_ids if item in by_id]
    if not answer_ids:
        return SelectionVerdict("missing", "none_selected")
    if target_date is not None:
        end = target_end_date or target_date
        # 有效期不覆盖目标日期的条目不是本轮合法候选：选中它按越界处理，交回现行证据计划；
        # 冲突分组也不纳入它（门禁实测：9 月 25 日问早餐，模型选了国庆期间条目）。
        if any(
            not _valid_during(by_id[item], target_date, end)
            for item in (*answer_ids, *related_ids)
        ):
            return SelectionVerdict("invalid", "outside_validity")
        candidates = [entry for entry in candidates if _valid_during(entry, target_date, end)]
    answers = [by_id[item] for item in dict.fromkeys(answer_ids)]
    topics = tuple(detect_property_topics(question))
    # 与现行证据计划同一口径：只对客人问到的钟点或收费检查冲突；其他属性的矛盾规则查不出。
    attributes = asked_attributes(question) & {"time", "fee"}

    def conflicting(entries: Sequence[Any]) -> str | None:
        """返回第一个出现矛盾的属性；主题认得出时只比较该主题该属性的分句。"""
        return _object_conflict(question, topics, attributes, entries)

    # 冲突与特殊时期优先只在同一对象内进行（修复 Spec §2.3(3)）：每个回答条目只与同一
    # 对象的合法候选比较；多个回答条目属于不同对象时各自检查、各自作答，不互相判冲突
    # （一楼、二楼健身房开放时间不同不是矛盾，Codex 审查 F5）。是否同组只看条目自身的范围、
    # 房源与问法，不看模型给的 related 标签（Codex 审查 B24-R6）。
    groups = [
        [
            answer,
            *(entry for entry in candidates if entry is not answer and same_object(entry, answer)),
        ]
        for answer in answers
    ]
    if target_date is not None:
        end = target_end_date or target_date
        if any(
            _crosses_validity_boundary(entry, target_date, end)
            for group in groups
            for entry in group
        ):
            return SelectionVerdict("invalid", "period_boundary")
    resolved: list[Any] = []
    for answer, group in zip(answers, groups, strict=True):
        conflict = conflicting(group)
        periodic = [entry for entry in group if _has_validity(entry)]
        if conflict and periodic and len(periodic) < len(group):
            # D6：同一对象的特殊时期政策与平时政策矛盾时以特殊时期为准；模型漏选期间条目时
            # 也以它作答。
            group = periodic
            conflict = conflicting(group)
            chosen = [answer] if _has_validity(answer) else periodic
        else:
            chosen = [answer]
        if conflict:
            return SelectionVerdict("missing", f"conflict_{conflict}")
        if target_date is not None and _period_veto(
            question, topics, attributes, chosen, candidates
        ):
            return SelectionVerdict("missing", "period_veto")
        resolved.extend(entry for entry in chosen if entry not in resolved)
    # 模型自己标出的条目参与否决（用户 2026-10-08 决定，1.70.0 候选门禁 K-洗衣-X）：选为回答
    # 或标为相关、但不属于同一对象的条目，在客人问到的收费或钟点上与回答矛盾时回未确认，
    # 只否决不替换。标签只能增加否决，不会让同对象内的冲突逃过上面的检查（B24-R6）。
    # 只在客人问题的主题认得出时比较，且只比较该主题该属性的分句：主题认不出时整段答案互比
    # 会误判（1.69.0 门禁：问安静时段，模型把客厅开放时间标为相关，不是钟点冲突）。
    # ponytail: 模型只选一条、不标另一条，或主题认不出时仍会漏检；一项里问两个对象（「一楼
    # 和二楼早餐厅分别几点开」）且两条都选为回答时也会被否决为未确认。要区分需另行确认
    # 主体关系依据。
    flagged = (
        [by_id[item] for item in dict.fromkeys((*answer_ids, *related_ids))] if topics else []
    )
    for answer in resolved:
        for other in flagged:
            if other is answer or same_object(other, answer):
                continue
            conflict = conflicting([answer, other])
            if conflict:
                return SelectionVerdict("missing", f"conflict_flagged_{conflict}")
    answers = resolved
    texts = [str(entry.answer) for entry in answers]
    if any(_INSTRUCTION_INJECTION.search(text) for text in texts):
        return SelectionVerdict("missing", "answer_needs_review")
    if not fits_guest_reply_parts(compose_static_reply(texts)) or (
        len(texts) > 1 and sum(len(text) for text in texts) > STATIC_REPLY_MAX_CHARS
    ):
        return SelectionVerdict("missing", "reply_exceeds_message_limit")
    return SelectionVerdict(
        "grounded",
        "selected",
        tuple(_knowledge_part(question, entry) for entry in answers),
    )


def compose_static_reply(answers: Sequence[str]) -> str:
    """把覆盖所问属性的审核答案按来源顺序拼成回复，不改写正文。"""
    return "\n".join(item.strip() for item in answers if item.strip())
