import re

from homestay_bot.domain.enums import Language

_ZH_HUMAN_CONTACT_REPLY = "我会立即联系管家来处理，请您稍等。"
_EN_HUMAN_CONTACT_REPLY = (
    "I’ll contact our on-duty host immediately. Please wait a moment."
)
_ZH_HIGH_RISK_ACKNOWLEDGEMENT = "您的情况我已记录。"
_ZH_HIGH_RISK_HANDOFF = "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
_EN_HIGH_RISK_ACKNOWLEDGEMENT = "I’ve recorded the situation."
_EN_HIGH_RISK_HANDOFF = (
    "I’ll contact the on-duty host immediately to follow up. "
    "Please keep your phone available."
)

_ZH_WEATHER_PATTERN = re.compile(r"天气|气温|温度|下雨|降雨|阵雨|雷雨|晴天")
_EN_WEATHER_PATTERN = re.compile(
    r"\b(?:weather|temperature|rain|storm|sunny|forecast)\b",
    re.IGNORECASE,
)
_ZH_UMBRELLA_PATTERN = re.compile(
    r"伞|雨衣|雨具|防雨"
)
_EN_UMBRELLA_PATTERN = re.compile(
    r"\b(?:umbrella|raincoat|rain[ -]?gear|waterproof)\b",
    re.IGNORECASE,
)
_PROPERTY_SELF_REFERENCE_PATTERN = re.compile(
    r"我们民宿|本民宿|咱们民宿|我们客栈|本客栈|本店|"
    r"民宿这边|客栈这边|民宿这儿|客栈这儿|"
    r"\bour (?:homestay|property|guesthouse|hotel)\b",
    re.IGNORECASE,
)
# 本店设施断言：场所词 + 供应或状态动词。自称词判据（上面那条）只认「我们民宿」
# 这类说法，而生产 2026-09-10 的天气回复写的是「民宿这边：大堂备有薄外套和雨伞，
# 需要随时说；房间已换秋被，觉得凉可再加一床。」——前半句的自称说法不在词表里，
# 后半句一个自称词都没有，两句都被放行，客人可能据此下楼索要并不存在的东西。
#
# 用「有没有提到民宿」来识别「有没有编造民宿事实」是不可靠的代理，这里改用危害
# 本身的形态：对本店场所里某样东西的供应或状态下断言。本函数只作用于联网／旅游
# 正文与改写路径，而联网搜索不可能返回本店信息，因此这条路径上出现的设施断言必然
# 是模型自行添加的，从严删除是安全的。
#
# 只覆盖中文：生产证据全部是中文，英文侧没有观察到同类写法，凭印象扩正则会在没有
# 证据的地方引入误删风险。
_PROPERTY_AMENITY_CLAIM_PATTERN = re.compile(
    r"(?:大堂|前台|房间|客房|楼下|楼上|院子|厨房|卫生间|浴室|阳台|公区|楼道)"
    r"[^。！？；;!?]{0,12}"
    r"(?:备有|备了|配有|配备|提供|放了|放着|已换|已备|可借|可以借|能借|免费)"
)
_ROOM_SALES_CTA_PATTERN = re.compile(
    r"如果.{0,12}(?:我|我们).{0,16}(?:推荐|介绍).{0,24}房型|"
    r"(?:我|我们).{0,12}(?:可以|能).{0,16}(?:推荐|介绍).{0,24}房型|"
    r"帮您.{0,16}(?:推荐|介绍|挑选).{0,24}房型",
    re.IGNORECASE,
)
_SENSITIVE_GUEST_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        r"(?![A-Za-z0-9.-])"
    ),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)"),
    re.compile(
        r"(?:订单号?|预订号|order(?:\s+number)?)\s*[:：#]?\s*[A-Za-z0-9-]{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"[\u4e00-\u9fff]{2,20}(?:路|街|道|巷)\d+号"),
)

# 这些模式只处理客人可见的、尚未由人工确认的执行结果承诺。
# “请立即离开房间”“拨打 119”等安全指令不在匹配范围内。
_UNSAFE_COMMITMENT_PATTERNS = (
    re.compile(
        r"(?:已经|已|马上|立即|尽快|稍后|这就|会).{0,18}"
        r"(?:安排|派|叫|通知|联系|上门|送|补|维修|修理|处理|解决|回复|"
        r"反馈|完成|查清|核实|确认|跟进|协助)"
    ),
    re.compile(
        r"(?:师傅|工作人员|员工|管家).{0,18}"
        r"(?:会|马上|立即|尽快|稍后|一定|保证).{0,18}"
        r"(?:上门|处理|解决|维修|查看|送|补|联系|完成)"
    ),
    re.compile(
        r"(?:一定|保证|肯定|彻底).{0,18}"
        r"(?:解决|处理|修好|送到|安排|完成|恢复)"
    ),
    re.compile(
        r"(?:arranged|technician\s+will|staff\s+will|will\s+(?:come|arrive|fix|"
        r"resolve|send|deliver|handle)|guarantee|make\s+sure\s+it\s+is\s+fixed)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:师傅|工作人员|员工|管家|维修人员).{0,12}(?:在路上|快到了|很快到)"),
    re.compile(
        r"(?:师傅|工作人员|员工|管家|维修人员).{0,12}"
        r"(?:正在赶来|正往.{0,6}赶|随后到|马上到|很快到|过来|上门)"
    ),
    re.compile(r"(?:今天|今晚|明天|稍后|马上|很快).{0,12}(?:修好|送到|处理好|解决好)"),
    re.compile(r"正在.{0,8}(?:安排|派|通知|联系).{0,12}(?:师傅|人员|员工|管家)"),
    re.compile(
        r"(?:technician|staff|host|someone).{0,18}"
        r"(?:is|are|'s)?\s*(?:on the way|coming|arriving|being sent)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:we(?:'re| are)|i(?:'m| am))\s+sending\s+someone", re.IGNORECASE),
)

_ZH_SAFE_HUMAN_SENTENCE = re.compile(
    r"(?:已收到|收到您的|记下|记录|抱歉|对不起|请先|请立即|不要|避免|"
    r"长按|按住|拔下|拔掉|断开|关闭|停止使用|离开房间|前往安全|拨打\s*119|"
    r"保持在安全区域|远离明火|开窗通风|切断燃气|切断电源|不要触碰|保持距离|"
    r"呼叫急救|尝试.{0,12}(?:按|关闭|重启|断电))"
)
_EN_SAFE_HUMAN_SENTENCE = re.compile(
    r"(?:thanks for letting us know|sorry|please\s+(?:leave|move|call|unplug|"
    r"disconnect|turn off|stop|avoid|press|hold|try))",
    re.IGNORECASE,
)
_ZH_HIGH_RISK_SAFETY_SENTENCE = re.compile(
    r"(?:请立即|请先|不要|避免|停止使用|离开|撤离|前往安全|"
    r"拨打\s*(?:119|110|120)|远离明火|开窗通风|切断燃气|切断电源|"
    r"不要触碰|保持距离|呼叫急救)"
)
# 英文侧原本只认 do not touch，于是燃气文案的第二句「Do not switch any electrical
# device on or off and do not use an open flame」被整句丢掉，客人只收到一半指令，
# 而且不会有任何报错。祈使句的否定形式基本都是安全动作，按动词列举放开；刻意不写成
# do not \w+，避免「do not worry」这类安抚被当成安全句留下。
_EN_HIGH_RISK_SAFETY_SENTENCE = re.compile(
    r"(?:please\s+(?:leave|move|call|unplug|disconnect|turn off|stop|avoid|open)|"
    r"call\s+(?:the\s+)?(?:police|fire department|emergency services)|"
    # 既有缺陷：英文火警文案的「Call 119 if there is fire or smoke.」不匹配上面
    # 那条按机构名列举的规则，一直被整句丢掉——英文客人遇到火灾从没收到过报警号码。
    r"call\s+(?:119|110|120|911|999|112)\b|"
    r"stay away|"
    r"do not\s+(?:touch|switch|use|move|enter|open|light|handle|attempt)|"
    r"open the windows?|turn off the main power)",
    re.IGNORECASE,
)

_ZH_FACILITY_FALLBACK = "请先停止使用该设施，不要拆卸或强行操作。"
_EN_FACILITY_FALLBACK = (
    "Please stop using the facility. Do not disassemble or force it."
)
_ZH_FACILITY_SUBMITTED = "我已提交管家人工处理，请您稍等。"
_EN_FACILITY_SUBMITTED = (
    "I've submitted this to the host for manual handling. Please wait a moment."
)
_ZH_UNSAFE_FACILITY_ACTION = re.compile(
    r"拆(?:开|卸)|打开.{0,6}(?:后盖|机盖|外壳)|"
    r"(?:接触|触碰).{0,6}(?:电线|线路|电路)|带电操作|"
    r"重(?:置|启).{0,8}(?:路由器|网关|房间设备)|"
    r"反复.{0,6}(?:点火|启动|通电|开关)|强腐蚀|疏通剂"
)
_EN_UNSAFE_FACILITY_ACTION = re.compile(
    r"disassembl|open.{0,16}(?:cover|casing)|touch.{0,12}(?:wire|circuit)|"
    r"work.{0,8}live|(?:reset|restart).{0,16}(?:router|gateway|room device)|"
    r"repeatedly.{0,12}(?:ignite|start|power)|corrosive|drain cleaner",
    re.IGNORECASE,
)
_ZH_NEGATED_FACILITY_ACTION = re.compile(
    r"(?:不要|切勿|请勿|避免|不得).{0,12}(?:"
    + _ZH_UNSAFE_FACILITY_ACTION.pattern
    + r")"
)
_EN_NEGATED_FACILITY_ACTION = re.compile(
    r"(?:do not|don't|never|avoid).{0,24}(?:"
    + _EN_UNSAFE_FACILITY_ACTION.pattern
    + r")",
    re.IGNORECASE,
)
_ZH_FACILITY_FOLLOW_UP = re.compile(
    r"请问|(?:能否|是否|可否).{0,8}(?:告知|说明|提供|描述)|"
    r"麻烦.{0,8}(?:告知|说明|提供|描述)"
)
_EN_FACILITY_FOLLOW_UP = re.compile(
    r"(?:could|can|would) you|please (?:tell|describe|provide)|do you know",
    re.IGNORECASE,
)
_ZH_FACILITY_SUBMISSION_CLAIM = re.compile(r"(?:已|已经).{0,8}提交.{0,8}(?:人工|管家)")
_EN_FACILITY_SUBMISSION_CLAIM = re.compile(
    r"(?:have|has|'ve|'s) submitted.{0,24}(?:host|manual)",
    re.IGNORECASE,
)


def human_contact_reply(language: Language) -> str:
    """返回无需人工确认执行结果的统一管家联系话术。"""
    if language is Language.EN:
        return _EN_HUMAN_CONTACT_REPLY
    return _ZH_HUMAN_CONTACT_REPLY


def _contains_ungrounded_property_claim(text: str) -> bool:
    """判断一段文字是否含未经审核的民宿自述、设施断言或房型推销。"""
    return bool(
        _PROPERTY_SELF_REFERENCE_PATTERN.search(text)
        or _PROPERTY_AMENITY_CLAIM_PATTERN.search(text)
        or _ROOM_SALES_CTA_PATTERN.search(text)
    )


def remove_ungrounded_property_claims(content: str) -> str:
    """逐句删除未经审核的民宿自述和无关房型推销。"""
    safe_lines: list[str] = []
    for line in content.splitlines():
        if not _contains_ungrounded_property_claim(line):
            safe_lines.append(line)
            continue
        # 保护数字列表的小数点，只按中英文句末拆分命中行。
        sentences = re.split(
            r"(?<=[。！？!?])|(?<=\.)\s+(?=[A-Z])",
            line,
        )
        safe_sentences = [
            sentence
            for sentence in sentences
            if sentence and not _contains_ungrounded_property_claim(sentence)
        ]
        if safe_sentences:
            safe_lines.append("".join(safe_sentences).strip())

    numbered_line = re.compile(
        r"^(?P<indent>\s*)(?P<number>\d{1,2})[.、．）)]\s*(?P<body>.+)$"
    )
    if sum(bool(numbered_line.match(line)) for line in safe_lines) >= 2:
        sequence = 0
        renumbered: list[str] = []
        for line in safe_lines:
            match = numbered_line.match(line)
            if match is None:
                renumbered.append(line)
                continue
            sequence += 1
            renumbered.append(
                f"{match.group('indent')}{sequence}. {match.group('body')}"
            )
        safe_lines = renumbered
    return "\n".join(safe_lines).strip()


def redact_sensitive_guest_text(content: str) -> str:
    """集中遮盖手机号、邮箱、身份证、订单号和精确门牌地址。"""
    redacted = content
    for pattern in _SENSITIVE_GUEST_PATTERNS:
        redacted = pattern.sub("[敏感信息已隐藏]", redacted)
    return redacted


def contains_sensitive_guest_text(content: str) -> bool:
    """判断客人可见或模型输入文本是否仍含本地可识别敏感字段。"""
    return any(pattern.search(content) for pattern in _SENSITIVE_GUEST_PATTERNS)


def _contains_unsafe_commitment(sentence: str) -> bool:
    """判断单句是否声称尚未确认的人员调度或处理结果。"""
    return any(pattern.search(sentence) for pattern in _UNSAFE_COMMITMENT_PATTERNS)


# 无人工介入时才收紧的两类承诺。现有 _UNSAFE_COMMITMENT_PATTERNS 要求「确定性副词
# + 动作动词」（马上安排、会尽快送、一定给您解决），下面这两类整类漏过。
#
# 只在 requires_human=False 分支生效：那一支意味着本轮不会产生任务、审批、提醒或
# 管家通知，回复里任何「我会去做 X」都无人兑现。requires_human=True 分支会追加
# 「我会立即联系管家来处理」，那时同样的话有人接手，不该删。
#
# 生产消息 127 同时踩中两类：「明天下午三点左右到没有问题的。我这边先帮您确认一下
# 安排。」——本轮任务 0、审批 0、提醒 0、无管家通知，而模型手里的订单是 8 月 14 至
# 16 日，与「明天」相差近一个月。
_SOFT_COMMITMENT_PATTERNS = (
    # 第一人称软承诺：不含确定性副词，但仍然把动作揽了下来。
    re.compile(
        r"(?:我|我们|我这边|这边)[^。！？；;!?]{0,12}"
        r"(?:帮您|给您|替您|帮你|好|先|再)[^。！？；;!?]{0,8}"
        r"(?:安排|确认|核实|处理|跟进|准备|留意|协调|落实|对接)"
    ),
    # 对客人提出的安排直接应允：系统没有任何记录，没人会照办。限定同句出现
    # 到店、入住、寄存一类安排词，避免误删「您这样理解没有问题」这类澄清。
    re.compile(
        r"(?:几点到|到店|到达|入住|退房|寄存|提前|延迟|延后|加床|加一床|换房"
        # 「三点左右到」这类时间+到达的说法同样是安排；但必须带「到」，否则
        # 「明天多云，出门没问题」这种天气建议会被一起删掉。
        r"|(?:今天|明天|后天|上午|下午|中午|晚上|傍晚|\d{1,2}[点时])"
        r"[^。！？；;!?]{0,10}到)"
        r"[^。！？；;!?]{0,16}"
        r"(?:没有问题|没问题|都可以|完全可以|可以的|没关系)"
    ),
)


# 免责表达：句子在说「以后续确认为准」，是在降低客人的期待而不是许下承诺。
# 生产消息 115 的「具体门锁和寄存安排以我当天给您的确认为准，您出发前再跟我对一下
# 时间就好。」曾被上面的软承诺判据误删——它含「我给您…确认」，但整句的作用恰好相反，
# 删掉比留着更糟。
_COMMITMENT_DISCLAIMER_PATTERN = re.compile(
    r"为准|不一定|无法保证|视.{0,6}而定|以.{0,10}(?:确认|通知|安排)为"
)


def _contains_soft_commitment(sentence: str) -> bool:
    """判断句子是否为无人兑现的软承诺或未经记录的应允。

    只覆盖「响应当前请求」这一类明确有害的承诺。刻意不追「如果您早到，我看看当天
    能不能先寄存」这类预告未来可能的说法：它们留了余地，危害有限，而其动词是开放
    集合，靠白名单穷举只会变成打地鼠。
    """
    if _COMMITMENT_DISCLAIMER_PATTERN.search(sentence):
        return False
    return any(pattern.search(sentence) for pattern in _SOFT_COMMITMENT_PATTERNS)


_SENTENCE_CHUNK = re.compile(r"[^。！？；;.!?]+[。！？；;.!?]*")


def _keeps_sentence(sentence: str) -> bool:
    """逐句过滤的唯一判定：非空，且不含执行承诺或软承诺。"""
    return (
        bool(sentence)
        and not _contains_unsafe_commitment(sentence)
        and not _contains_soft_commitment(sentence)
    )


def _safe_text_keeping_breaks(content: str, separator: str) -> str:
    """与 `_safe_sentences` 的取舍完全相同，但保留句子之间原有的换行。

    旧写法把每句 `strip()` 后首尾相接，紧跟句号的换行和空行全部丢失，模型分好的
    【小节】和本地拼上的时效说明都被压成一段。这里只额外记住每句前面的换行数：
    被删掉的句子把它的分段让给下一句，避免删句后两段粘在一起或留下空行。
    """
    parts: list[str] = []
    pending_breaks = 0
    for chunk in _SENTENCE_CHUNK.findall(content):
        sentence = chunk.strip()
        leading = chunk[: len(chunk) - len(chunk.lstrip())]
        pending_breaks = max(pending_breaks, min(leading.count("\n"), 2))
        if not _keeps_sentence(sentence):
            continue
        if parts:
            parts.append("\n" * pending_breaks if pending_breaks else separator)
        parts.append(sentence)
        pending_breaks = 0
    return "".join(parts).strip()


def _safe_sentences(content: str) -> list[str]:
    """按句删除承诺，同时保留撤离提示和低风险自助建议。

    本函数只在 requires_human=False 时被调用，即本轮不会有任何人接手；因此除了
    既有的执行承诺，软承诺与未经记录的应允也一并删除。
    """
    return [
        sentence.strip()
        for sentence in _SENTENCE_CHUNK.findall(content)
        if _keeps_sentence(sentence.strip())
    ]


def _safe_human_sentences(content: str, language: Language) -> list[str]:
    """人工场景只保留中性确认、歉意和明确的低风险安全指令。"""
    safe_pattern = (
        _EN_SAFE_HUMAN_SENTENCE if language is Language.EN else _ZH_SAFE_HUMAN_SENTENCE
    )
    return [
        sentence
        for sentence in _safe_sentences(content)
        if safe_pattern.search(sentence)
    ]


def _contains_unsafe_facility_action(sentence: str, language: Language) -> bool:
    """忽略明确禁止语后，判断模型是否仍建议客人执行危险操作。"""
    if language is Language.EN:
        remaining = _EN_NEGATED_FACILITY_ACTION.sub("", sentence)
        return bool(_EN_UNSAFE_FACILITY_ACTION.search(remaining))
    remaining = _ZH_NEGATED_FACILITY_ACTION.sub("", sentence)
    return bool(_ZH_UNSAFE_FACILITY_ACTION.search(remaining))


def _contains_facility_follow_up_or_submission(
    sentence: str,
    language: Language,
) -> bool:
    """拦截模型追问和自行声明提交状态，由本地流程统一决定。"""
    if language is Language.EN:
        return bool(
            _EN_FACILITY_FOLLOW_UP.search(sentence)
            or _EN_FACILITY_SUBMISSION_CLAIM.search(sentence)
        )
    return bool(
        _ZH_FACILITY_FOLLOW_UP.search(sentence)
        or _ZH_FACILITY_SUBMISSION_CLAIM.search(sentence)
    )


# 设施建议逐条检查用的规则。故障应急应是直接动作；带条件的建议最容易写反
# （「如果还能用，就去别处」），整类去掉，而不是逐种说法打补丁。
_ZH_CONDITIONAL = re.compile(r"如果|假如|假设|万一|要是|倘若|一旦|只要|的话|若(?!干)")
_EN_CONDITIONAL = re.compile(r"\b(?:if|unless|in\s+case|whenever)\b", re.IGNORECASE)
_FACILITY_GREETING_ONLY = re.compile(
    r"^(?:您好|你好|hello|hi|hey)[\s，,!！。.]*$", re.IGNORECASE
)
# 只在设施建议里使用：共用承诺过滤若加入「X 分钟内到」，会误删「步行 10 分钟到地铁站」。
_ZH_FACILITY_TIME_PROMISE = re.compile(
    r"(?:分钟|小时).{0,6}(?:到|赶到|上门|修好|处理好)"
)
_EN_FACILITY_TIME_PROMISE = re.compile(
    r"\b(?:minutes?|hours?)\b.{0,20}\b(?:arrive|come|fix|repair)", re.IGNORECASE
)
_FACILITY_ITEM_MARKER = re.compile(r"^(?:[•·\-*]|\d{1,2}[.、)）])\s*")
_FACILITY_ITEM_ACKNOWLEDGEMENT = re.compile(r"^(?:收到|好的)[，,、\s]*")
_FACILITY_ITEM_SPLIT = re.compile(r"(?<=[。！？；;!?])")
_FACILITY_ITEM_END = re.compile(r"[。．.！!；;，,、\s]+$")
FACILITY_ADVICE_MAX_ITEMS = 2
_ZH_FACILITY_ITEM_MAX_CHARS = 40
_EN_FACILITY_ITEM_MAX_CHARS = 120


def _clean_facility_item(item: str, language: Language) -> str | None:
    """规整并检查一条设施建议；不合格返回 None。"""
    text = _plain_text_guest_reply(item).strip()
    text = _FACILITY_ITEM_MARKER.sub("", text)
    text = _FACILITY_ITEM_ACKNOWLEDGEMENT.sub("", text)
    text = _FACILITY_ITEM_END.sub("", text)
    if not text or _FACILITY_GREETING_ONLY.match(text):
        return None
    limit = (
        _EN_FACILITY_ITEM_MAX_CHARS if language is Language.EN else _ZH_FACILITY_ITEM_MAX_CHARS
    )
    conditional = _EN_CONDITIONAL if language is Language.EN else _ZH_CONDITIONAL
    time_promise = (
        _EN_FACILITY_TIME_PROMISE if language is Language.EN else _ZH_FACILITY_TIME_PROMISE
    )
    if (
        len(text) > limit
        or "?" in text
        or "？" in text
        or conditional.search(text)
        or time_promise.search(text)
        or not _keeps_sentence(text)
        or _contains_unsafe_facility_action(text, language)
        or _contains_facility_follow_up_or_submission(text, language)
    ):
        return None
    return text


def prepare_facility_advice_reply(
    advice: list[str] | None,
    language: Language,
) -> str:
    """用模型给出的建议清单组装设施故障回复，开头、结尾与标点全部由本地负责。

    只在维修任务创建成功后调用，因此收尾的「已提交管家人工处理」一定为真。
    清单缺失或逐条检查后一条不剩时，使用固定兜底，不发送空回复。
    """
    # 一条建议里写了几句时拆开逐句检查：夹带的承诺只删那一句，安全建议保留。
    candidates = [
        part
        for item in advice or []
        if isinstance(item, str)
        # 先去 Markdown 再拆句：成对的 ** 跨越句号时，先拆会让它无法去掉。
        for part in _FACILITY_ITEM_SPLIT.split(_plain_text_guest_reply(item))
    ]
    kept: list[str] = []
    for candidate in candidates:
        cleaned = _clean_facility_item(candidate, language)
        if cleaned is not None and cleaned not in kept:
            kept.append(cleaned)
        if len(kept) >= FACILITY_ADVICE_MAX_ITEMS:
            break
    if language is Language.EN:
        body = " ".join(f"{item}." for item in kept) or _EN_FACILITY_FALLBACK
        return f"Thanks for letting us know. {body} {_EN_FACILITY_SUBMITTED}"
    body = "".join(f"{item}。" for item in kept) or _ZH_FACILITY_FALLBACK
    return f"收到，{body}{_ZH_FACILITY_SUBMITTED}"


def _high_risk_reply(content: str, language: Language) -> str:
    """生成不含道歉、责任判断或结果承诺的高危转人工回复。"""
    if language is Language.EN:
        acknowledgement = _EN_HIGH_RISK_ACKNOWLEDGEMENT
        handoff = _EN_HIGH_RISK_HANDOFF
        safe_pattern = _EN_HIGH_RISK_SAFETY_SENTENCE
        separator = " "
    else:
        acknowledgement = _ZH_HIGH_RISK_ACKNOWLEDGEMENT
        handoff = _ZH_HIGH_RISK_HANDOFF
        safe_pattern = _ZH_HIGH_RISK_SAFETY_SENTENCE
        separator = ""

    # 固定确认和收尾可能已经由上游生成；先删除再提取安全动作，保证幂等。
    content_without_fixed_text = content.strip()
    for fixed_text in (acknowledgement, handoff):
        content_without_fixed_text = content_without_fixed_text.replace(fixed_text, "")
    safety_sentences: list[str] = []
    for sentence in _safe_sentences(content_without_fixed_text):
        if not safe_pattern.search(sentence):
            continue
        # 安全动作可能与道歉或责任判断写在同一句；仅删除态度片段，保留动作。
        if language is Language.EN:
            sentence = re.sub(
                r"\b(?:sorry|we apologize)(?:\s+for[^,.!?]*)?[,.:;!?]*\s*",
                "",
                sentence,
                flags=re.IGNORECASE,
            )
        else:
            sentence = re.sub(
                r"(?:真的|非常|十分|很)?(?:抱歉|对不起)(?:给您[^，。！？]*)?[，。！？]*",
                "",
                sentence,
            )
            sentence = re.sub(
                r"(?:这|此事)?是(?:我们|民宿|店里)的责任[，。！？]*",
                "",
                sentence,
            )
        if sentence.strip():
            safety_sentences.append(sentence.strip())
    # 用语言分隔符而非空串连接：中文句间不留空格，英文句号后必须有空格，否则会
    # 拼成「immediately.Call 119」这种粘连，影响所有英文高危回复。
    safety_text = separator.join(safety_sentences).strip()
    prefix = f"{safety_text}{separator}" if safety_text else ""
    return f"{prefix}{acknowledgement}{separator}{handoff}"


def _is_weather_question(question: str, language: Language) -> bool:
    """判断当前问题是否明确询问天气，避免给其他回复误加天气开场。"""
    pattern = _EN_WEATHER_PATTERN if language is Language.EN else _ZH_WEATHER_PATTERN
    return pattern.search(question) is not None


def _warm_weather_reply(content: str, language: Language) -> str:
    """为已取得的天气事实增加简短管家表达，不改写任何查询字段。"""
    if language is Language.EN:
        opener = "I checked the forecast for you. "
        if not content.startswith(opener):
            content = f"{opener}{content}"
        if re.search(
            r"\b(?:rain|shower|storm)\b",
            content,
            re.IGNORECASE,
        ) and not _EN_UMBRELLA_PATTERN.search(content):
            content = f"{content.rstrip()} It’s a good idea to bring an umbrella."
        return content

    opener = "我帮您看了一下，"
    if not content.startswith("我帮您看了一下"):
        content = f"{opener}{content}"
    if re.search(r"下雨|降雨|阵雨|雷雨", content) and not _ZH_UMBRELLA_PATTERN.search(
        content
    ):
        content = f"{content.rstrip()}出门记得带伞。"
    return content


def _plain_text_guest_reply(content: str) -> str:
    """移除明确 Markdown 强调和列表符，保留合法星号与下划线。"""
    cleaned = re.sub(r"\*{3}(?=\S)([^\n*]*?\S)\*{3}", r"\1", content)
    cleaned = re.sub(r"_{3}(?=\S)([^\n_]*?\S)_{3}", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^\n*]+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^\n_]+?)__", r"\1", cleaned)
    cleaned = re.sub(r"\*(?=\S)([^\n*]*?\S)\*", r"\1", cleaned)
    cleaned = re.sub(r"_(?=\S)([^\n_]*?\S)_", r"\1", cleaned)
    return re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", cleaned)


# 小节标题：紧跟在句末标点或冒号之后、不超过 8 个字的【……】。句中的【平安武汉】
# 这类名称前面没有句末标点，不会被当成标题。
_INLINE_SECTION_HEADER = re.compile(
    r"(?<=[。！？；;!?：:])[ \t]*\n?(?=【[^【】\n]{1,8}】)"
)
# 本地拼接的时效说明（见 integrations/tourism.py 的自然收尾），固定另起一段。
_EVIDENCE_FOOTER_LEAD = re.compile(
    r"(?<=[^\n])[ \t]*\n?(?=这是我今天（\d{1,2}月\d{1,2}日）帮您查到的|I checked this latest )"
)
_INLINE_BULLET_AFTER_TEXT = re.compile(r"(?<=\S)[ \t]*•[ \t]+")
_INLINE_BULLET_AFTER_PUNCTUATION = re.compile(r"(?<=[：:。；;！？!?])•")


def layout_guest_reply(content: str, language: Language) -> str:
    """只调整换行的确定性排版：小节标题与时效说明另起一段，列表项各占一行。

    不增删任何文字，去掉空白后与输入逐字相同；模型输出不稳定时，版式仍由这里兜底。
    """
    del language  # 中英文规则相同；保留参数便于以后按语言区分
    laid_out = _INLINE_SECTION_HEADER.sub("\n\n", content)
    laid_out = _EVIDENCE_FOOTER_LEAD.sub("\n\n", laid_out)
    laid_out = _INLINE_BULLET_AFTER_TEXT.sub("\n• ", laid_out)
    laid_out = _INLINE_BULLET_AFTER_PUNCTUATION.sub("\n•", laid_out)
    laid_out = re.sub(r"[ \t]+\n", "\n", laid_out)
    laid_out = re.sub(r"\n{3,}", "\n\n", laid_out)
    return laid_out.strip()


def prepare_guest_reply(
    content: str,
    *,
    language: Language,
    requires_human: bool,
    question: str = "",
    high_risk: bool = False,
) -> str:
    """生成唯一客人可见正文，并统一风格、承诺过滤和高危边界。"""
    content = _plain_text_guest_reply(content)
    if requires_human and high_risk:
        return _high_risk_reply(content, language)

    prepared = sanitize_guest_reply(
        content,
        language=language,
        requires_human=requires_human,
    )
    if not requires_human and _is_weather_question(question, language):
        prepared = _warm_weather_reply(prepared, language)
    return layout_guest_reply(prepared, language)


def sanitize_guest_reply(
    content: str,
    *,
    language: Language,
    requires_human: bool,
) -> str:
    """清除客人侧执行承诺，并在需要人工时追加唯一管家收尾。"""
    if requires_human:
        handoff = human_contact_reply(language)
        separator = " " if language is Language.EN else ""
        # 同一文本可能依次经过模型适配器和会话出口；先移除既有固定收尾，
        # 再统一过滤并追加，保证重复清洗不改变正文或误删前置歉意。
        content_without_handoff = content.strip()
        if content_without_handoff.endswith(handoff):
            content_without_handoff = content_without_handoff[: -len(handoff)].rstrip()
        safe_content = separator.join(
            _safe_human_sentences(content_without_handoff, language)
        ).strip()
        if not safe_content:
            acknowledgement = (
                "Thanks for letting us know."
                if language is Language.EN
                else "我已收到您的诉求。"
            )
            return f"{acknowledgement}{separator}{handoff}"
        return f"{safe_content}{separator}{handoff}"
    separator = " " if language is Language.EN else ""
    safe_content = _safe_text_keeping_breaks(content, separator)
    if safe_content:
        return safe_content
    if language is Language.EN:
        return "I’m unable to confirm that information right now."
    return "这项信息暂时无法确认。"
