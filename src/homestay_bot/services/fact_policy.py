"""不得编造事实：全项目唯一的规则定义。

关于民宿本身的任何信息——位置与周边、设施、物品、服务、规则、价格、房态、人员
安排——只能来自本轮提供的依据：审核知识、百居易查询结果、系统登记的本店事实。
没有依据就不写。本模块同时提供两层落实：

1. 提示词层：`FACT_SOURCE_RULE_ZH` / `FACT_SOURCE_RULE_EN`，所有面向客人的模型
   提示词都引用它们（`tests/unit/test_fact_policy.py` 有架构测试守住）。
2. 确定性层：`is_unsourced_homestay_claim` 按「这句话说的是谁」判定，只用于没有
   民宿依据的回复——提到民宿一侧却说不出来源的句子，默认不放行。
3. 店外状态：`is_unsourced_external_state_claim` 拦下没有本轮实时查询作依据、却断言
   会变化的店外状态的句子（「这几天武汉早晚偏凉」），见 1.39.17 P14。
4. 出处核对：`is_supported_by` 判断一句话能否在本轮依据（交给模型的审核知识、
   被改写的原文）里找到出处。有出处的本店事实不再被第 2 层删掉——1.39.15 起
   审核知识的原句在改写重发和非专属问题里被当成编造删空，就是因为过滤时不知道
   本轮有哪些依据。

过去按说法类型逐条补规则（1.39.12 设施、1.39.13 服务），下一类说法照样漏；
1.39.14 验收又漏了「您住的是江汉路附近的话」这种位置编造。判定改为看主体，
编造类型再多，只要说的是民宿就会被拦。锚点词表仍是有限的，不提民宿的暗示
（只写「附近有地铁站」）仍会漏，见 Spec docs/specs/2026-09-24_fact-source-rule-spec.md。
"""

import re

from homestay_bot.services.knowledge_service import detect_property_topics

# 系统登记的本店事实：模型可以直接说，不需要本轮依据。
SYSTEM_FACTS_ZH = "本店位于武汉，共 7 间房"

FACT_SOURCE_RULE_ZH = (
    "不得编造事实：关于民宿本身的任何信息——位置与周边、设施、物品、服务、规则、"
    "价格、房态、人员安排——只能来自本轮提供的审核知识、百居易查询结果或系统登记的"
    f"本店事实（{SYSTEM_FACTS_ZH}）；没有依据就不写，或说明需要确认。"
    "不要推测客人住在哪里，也不要推测民宿的位置和周边。"
    "店外信息只写有来源的内容或公认的常识；会变化的店外状态（天气、气温、降雨、人流、"
    "排队、路况、营业或开放状态、活动安排）只能来自本轮的实时查询结果，没有查询结果就"
    "说暂时查不到，不按季节或经验推测。"
)
FACT_SOURCE_RULE_EN = (
    "Never invent facts. Anything about the homestay itself - its location and "
    "surroundings, facilities, items, services, rules, prices, availability, or "
    "staff arrangements - may only come from the reviewed knowledge, live lookup "
    "results, or registered facts provided in this turn (the homestay is in Wuhan "
    "and has 7 rooms). Without such a source, leave it out or say it needs "
    "confirmation. Do not guess where the guest is staying or where the homestay "
    "is. For anything outside the homestay, state only sourced information or "
    "well-known general knowledge. Changing conditions outside the homestay - weather, "
    "temperature, rain, crowds, queues, traffic, opening status, events - may only come "
    "from live lookup results in this turn; without them, say you cannot check right "
    "now instead of guessing from the season or experience."
)

# ---- 确定性层 -------------------------------------------------------------

# 民宿一侧的主体：本店自称、客人的住处、店内空间与人员。只描述「说的是谁」。
_ZH_HOMESTAY_ANCHOR = re.compile(
    r"民宿|本店|店里|客栈|我们这(?:儿|里|边)|咱们(?:这|家)|我们家|"
    r"我们(?=备|有|提供|配|可以提供|这)|"
    r"您住|你住|住的(?:是|地方|附近|那边)|住处|下榻|入住的(?:地方|房)|"
    r"房间|客房|屋里|房内|前台|大堂|玄关|门口|楼下|楼上|院子|厨房|"
    r"公区|公共区域|楼道|管家|保洁"
)
_EN_HOMESTAY_ANCHOR = re.compile(
    r"\b(?:our (?:homestay|place|property|guesthouse|house)|the homestay|"
    r"your (?:room|stay|accommodation)|in (?:the|your) room|front desk|reception|"
    r"lobby|the host|our host|we (?=have|provide|offer|keep|can arrange|stock))",
    re.IGNORECASE,
)

# 第 1 步：供应或服务的说法——一定是在陈述民宿有什么、能做什么。
_ZH_SUPPLY_OR_SERVICE = re.compile(
    r"备有|备着|备了|备好|常备|已备|配有|配备|提供|放了|放着|放在|已换|"
    r"可借|可以借|能借|借用|免费|赠送|取用|领取|"
    r"(?:找|跟|问)我(?:拿|取|要|借)|有(?:备用|一次性|免费)|"
    r"叫车|接送|代订|代购|预约|寄存|送到|送餐"
)
_EN_SUPPLY_OR_SERVICE = re.compile(
    r"\b(?:have|has|provide[sd]?|offer(?:s|ed)?|keep|stock(?:ed)?|free|"
    r"borrow|lend|pick up|arrange|available|complimentary|shuttle|deliver)\b",
    re.IGNORECASE,
)

# 第 2 步：不陈述事实的句式——问句、请客人提供信息、祝福、自我介绍、禁止性提醒。
_ZH_NON_ASSERTION = re.compile(
    r"[？?]|告诉我|您方便|可以告诉|跟我说|随时说|^祝|祝您|我是.{0,12}管家|"
    r"(?:不要|别|请勿|切勿|避免)[^。！？]{0,12}(?:房间|屋里|房内)|"
    r"(?:房间|屋里|房内)[^。！？]{0,6}(?:不要|别|请勿|切勿|避免)"
)
_EN_NON_ASSERTION = re.compile(
    r"\?|let me know|tell me|\b(?:enjoy|have a (?:great|nice|good))\b|"
    r"\b(?:do not|don't|never|avoid)\b[^.!?]{0,60}\b(?:room|homestay)\b",
    re.IGNORECASE,
)

# 第 3 步：相对位置——民宿地址没有交给模型，任何「离民宿多远、在哪一带」都是编造。
_ZH_RELATIVE_LOCATION = re.compile(
    r"附近|周边|一带|片区|位于|离|距离|步行|直达|几站|"
    r"\d+\s*(?:分钟|公里|米|站)|[一二两三四五六七八九十]+分钟|"
    r"在.{0,6}(?:路|街|区)"
)
_EN_RELATIVE_LOCATION = re.compile(
    r"\b(?:near|nearby|close to|next to|located|minutes?|km|kilometers?|"
    r"meters?|walk(?:ing)?|blocks?|district|area)\b",
    re.IGNORECASE,
)

# 第 4 步：只把民宿当作客人的去处，不附带任何断言。
_ZH_PLACE_ONLY = re.compile(
    r"(?:回|返回|回到)(?:民宿|住处|房间)|(?:从|离开)(?:民宿|住处)|"
    r"把民宿设为|(?:联系|找|告诉|问)(?:管家|工作人员)"
)
_EN_PLACE_ONLY = re.compile(
    r"\b(?:back to|return to|head back to|from) (?:the homestay|your room)\b|"
    r"\b(?:contact|ask|message) (?:the|our) host\b",
    re.IGNORECASE,
)

_STANDALONE_HEADING = re.compile(r"^\s*【[^】]{1,12}】\s*$")


def is_unsourced_homestay_claim(sentence: str) -> bool:
    """在没有民宿依据的回复里，判断这一句是否在陈述说不出来源的民宿信息。

    只看提到民宿一侧的句子，按顺序判断：供应或服务 → 删；问句、请求、祝福、
    自我介绍、禁止性提醒 → 留；相对位置或民宿设施话题 → 删；只把民宿当去处 → 留；
    其余 → 删（默认不放行）。单独成行的小节标题不参与判定。
    """
    text = sentence.strip()
    if not text or _STANDALONE_HEADING.match(text):
        return False
    zh = _ZH_HOMESTAY_ANCHOR.search(text) is not None
    en = _EN_HOMESTAY_ANCHOR.search(text) is not None
    if not zh and not en:
        return False
    if zh:
        if _ZH_SUPPLY_OR_SERVICE.search(text):
            return True
        if _ZH_NON_ASSERTION.search(text):
            return False
        if _ZH_RELATIVE_LOCATION.search(text) or detect_property_topics(text):
            return True
        return not _ZH_PLACE_ONLY.search(text)
    if _EN_SUPPLY_OR_SERVICE.search(text):
        return True
    if _EN_NON_ASSERTION.search(text):
        return False
    if _EN_RELATIVE_LOCATION.search(text) or detect_property_topics(text):
        return True
    return not _EN_PLACE_ONLY.search(text)


def is_supply_or_service_claim(sentence: str) -> bool:
    """只做第 1 步：提到民宿一侧并声称有什么、能提供什么服务。

    用于设施故障建议：建议本来就要说房间里的设备（「检查房间空调遥控器」），
    完整判定会把它当作设施话题删掉；这里只拦「前台有备用吹风机」这类供应说法。
    """
    text = sentence.strip()
    if _ZH_HOMESTAY_ANCHOR.search(text) and _ZH_SUPPLY_OR_SERVICE.search(text):
        return True
    return bool(_EN_HOMESTAY_ANCHOR.search(text) and _EN_SUPPLY_OR_SERVICE.search(text))


# 出处核对的两条标准：句子的字符二元组至少六成出现在依据里，且每个数字都在依据里。
# 原型实测：审核原文换说法后的覆盖率约 0.65 至 0.87，依据里没有的编造句约 0.27。
_SUPPORT_MIN_COVERAGE = 0.6
_SUPPORT_NOISE = re.compile(r"[\W_]+")
_SUPPORT_NUMBER = re.compile(r"\d+(?:[.:：]\d+)?")


def _support_bigrams(text: str) -> set[str]:
    """去掉空白与标点后取相邻两字，作为与说法无关的字面指纹。"""
    compact = _SUPPORT_NOISE.sub("", text.lower())
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def is_supported_by(sentence: str, source: str) -> bool:
    """判断一句话能否在依据文本中找到出处：字面覆盖足够，且数字全部出现在依据里。

    ponytail: 二元组覆盖只看字面，看不出语义——「有 24 小时便利店」和「没有 24 小时
    便利店」几乎一样。改写路径另有 `_validate_facts` 的否定核对，生成路径没有；
    依据条数多时判定也会变宽。真实模型回归出现依据内的语义篡改时，换成逐句蕴含判定。
    """
    if not source.strip():
        return False
    grams = _support_bigrams(sentence)
    if not grams:
        return _SUPPORT_NOISE.sub("", sentence) in _SUPPORT_NOISE.sub("", source)
    coverage = len(grams & _support_bigrams(source)) / len(grams)
    if coverage < _SUPPORT_MIN_COVERAGE:
        return False
    return set(_SUPPORT_NUMBER.findall(sentence)) <= set(_SUPPORT_NUMBER.findall(source))


# P14：会变化的店外状态只能来自本轮实时查询。1.39.16 测试号验收中，「明天天气怎么样？
# 还有空房吗？」只查了房态，回复却写了「这几天武汉早晚偏凉」。判据是「时间指向 + 会变化
# 的状态」同时出现且是断言：时间指向可以在句中任意分句，状态与非断言标记按分句判断，
# 这样「这几天早晚偏凉，建议带件外套」仍会被拦，「明天下雨的话可以去博物馆」则保留。
_ZH_TIME_ANCHOR = re.compile(
    r"这几天|这两天|最近|近期|近日|今天|今日|今晚|今早|明天|明早|明晚|后天|"
    r"这周|本周|这个周末|周末|现在|目前|此刻|这会儿|当下"
)
_EN_TIME_ANCHOR = re.compile(
    r"\b(?:these days|lately|recently|today|tonight|tomorrow|this week(?:end)?|"
    r"right now|currently)\b",
    re.IGNORECASE,
)
# 不收「冷」「热」「雨」「挤」「堵」这类单字：「今晚热水正常」「雨伞」会被误伤。
_ZH_CHANGING_STATE = re.compile(
    r"偏凉|偏冷|偏热|凉快|凉爽|转凉|闷热|炎热|寒冷|很冷|较冷|有点冷|很热|较热|有点热|"
    r"降温|升温|气温|温度|下雨|有雨|阵雨|雷阵雨|雷雨|降雨|晴天|放晴|晴朗|多云|阴天|"
    r"刮风|大风|起雾|雾霾|空气质量|人很多|人多|人少|排队|拥挤|堵车|拥堵|路况|"
    r"开门|关门|营业|歇业|闭馆|开放|花开|开花|花期|灯光秀|演出"
)
_EN_CHANGING_STATE = re.compile(
    r"\b(?:chilly|cold|hot|warm|humid|rain(?:y|ing)?|showers?|sunny|cloudy|windy|"
    r"crowded|busy|queues?|traffic|open|closed)\b",
    re.IGNORECASE,
)
_ZH_NON_ASSERTION_CLAUSE = re.compile(
    r"吗|呢|？|\?|是否|会不会|有没有|如果|若是|要是|的话|万一|假如|"
    r"留意|关注|查看|查一下|看一下|看一眼|看看|预报|为准|"
    r"查不到|查不了|没法查|暂时没法|无法确认|无法查询|没有实时|暂无|不确定|不清楚|说不准"
)
_EN_NON_ASSERTION_CLAUSE = re.compile(
    r"\?|\bif\b|whether|check|forecast|can't|cannot|unable|not sure|don't have",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"[，,；;：:]")


def is_unsourced_external_state_claim(sentence: str) -> bool:
    """判断一句话是否在没有实时依据时断言会变化的店外状态（天气、人流、营业等）。

    只在本轮没有实时查询结果的回复上使用；有联网结果时这些事实有来源，不走这里。
    问句、建议查看、条件句和「查不到」的说明不算断言；不带时间指向的稳定常识
    （「黄鹤楼在武昌区」「武汉夏天通常比较热」）也不算。

    ponytail: 词表判定，改述（「外面有点凉飕飕的」）会漏；由部署前真实模型回归兜底，
    漏报集中出现时再引入语义判定。
    """
    text = sentence.strip()
    if not text or text.endswith(("？", "?", "吗", "吗。", "呢", "呢。")):
        return False
    zh = _ZH_TIME_ANCHOR.search(text) is not None
    en = _EN_TIME_ANCHOR.search(text) is not None
    if not zh and not en:
        return False
    state = _ZH_CHANGING_STATE if zh else _EN_CHANGING_STATE
    non_assertion = _ZH_NON_ASSERTION_CLAUSE if zh else _EN_NON_ASSERTION_CLAUSE
    return any(
        state.search(clause) and not non_assertion.search(clause)
        for clause in _CLAUSE_SPLIT.split(text)
    )
