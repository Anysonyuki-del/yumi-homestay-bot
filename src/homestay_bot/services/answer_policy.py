import re
from dataclasses import dataclass
from typing import Literal

from homestay_bot.domain.enums import BusinessTaskType
from homestay_bot.services.knowledge_service import detect_property_topics
from homestay_bot.services.turn_plan import REQUEST_KINDS, PlanOutcome, plan_risks, usable_plan

# 住宿意图：问能不能住、有没有房。联网分流、房态工具开放和交易判定共用这一处定义。
# 以前各处各有一份词表，1.39.16 只在分流处补了「能住」「几个人住」，房态工具那几处
# 没跟上：问题不再被送去联网，却也调不到百居易，模型只能追问或说查不到。
_STAY_INTENT_PATTERN = re.compile(
    # 「还有空房吗」「订满了吗」与「有房」同义，都要走实时房态，不能由模型猜。
    r"有房|空房|余房|剩房|满房|订满|几间房|房态|可订|可用房|能住|可以住|住得下|"
    # 「今晚4个人住，有合适的房吗」问的是有没有房；单说「三个人住」只是人数，
    # 不算——否则加床、早餐这类问题会被当成房态交易，审核知识被剔除。
    r"有[^，。？?！!\s]{0,4}的房|"
    # 三位房号后几个字内问「还有、空着」；「还有票」问的是演出门票。
    r"(?<!\d)[1-9]\d{2}(?!\d)(?:号房|房)?[^\d，。？?！!]{0,6}(?:还有(?!票)|空着)|"
    r"availability|vacanc|\brooms?\b[^.?!\n]{0,20}\bavailable\b|"
    r"\bavailable\b[^.?!\n]{0,12}\brooms?\b",
    re.IGNORECASE,
)

_TRANSACTION_PATTERN = re.compile(
    r"价格|房价|多少钱|参考价|退款|退多少|"
    r"取消|改期|付款|支付|到账|订单|预订状态|发票金额|"
    r"availability|room rate|price|refund|cancel|reschedule|"
    # 英文房费问法也属于交易：限定「how much is/for/does…room」这类问价结构，
    # 不把「how much space in the room」「room service」当房价。
    r"\bhow\s+much\s+(?:is|are|does|do|would|will|for|to\s+(?:book|stay|rent))\b"
    r"[^?.!\n]{0,40}\brooms?\b(?!\s+service)|"
    r"payment|reservation status|invoice amount",
    re.IGNORECASE,
)

_PROPERTY_SPECIFIC_PATTERN = re.compile(
    r"你们|你家|民宿|房间|店里|停车|早餐|宠物|加床|电梯|"
    r"厨房|洗衣|发票|接送|无障碍|吸烟|行李寄存|寄存行李|"
    r"离.+(?:多远|多久)|距离|设施|服务|"
    r"your homestay|your property|parking|breakfast|pet|extra bed|"
    r"elevator|kitchen|laundry|invoice|pickup|accessible|smoking|"
    r"luggage storage|distance",
    re.IGNORECASE,
)

_HIGH_RISK_PATTERNS = (
    (
        "refund",
        re.compile(r"退款|退钱|退费|退全款|退全额|退多少|refund", re.IGNORECASE),
    ),
    (
        "complaint",
        re.compile(r"投诉|差评|举报|complain|complaint", re.IGNORECASE),
    ),
    (
        "early_check_in",
        re.compile(r"提前入住|提前住|early check[ -]?in", re.IGNORECASE),
    ),
    (
        "agitated",
        re.compile(
            r"太离谱|气死|愤怒|必须马上|立刻解决|受够了|"
            r"ridiculous|furious|unacceptable|!!!|！！！",
            re.IGNORECASE,
        ),
    ),
)

_LODGING_PRICE_PATTERN = re.compile(
    r"房价|住宿价格|民宿价格|房型价格|最低价|"
    r"(?:民宿|住宿|房间|房型|入住|预订).{0,12}"
    r"(?:多少钱|价格|优惠|便宜)|"
    r"(?:多少钱|价格|优惠|便宜).{0,12}"
    r"(?:民宿|住宿|房间|房型|入住|预订)|"
    r"(?:room|homestay|hotel).{0,12}(?:price|rate|discount)",
    re.IGNORECASE,
)

# 问房价：联网分流、参考价工具开放与强制调用共用这一处定义（1.40.0）。以前工具开放只认
# 中文与 room rate，英文问价开不出参考价工具，交易判定却认得出，结果一律转人工。
_ROOM_PRICE_PATTERN = re.compile(
    r"房价|房费|参考价|住宿价格|民宿价格|房型价格|最低价|最便宜|多少钱|几多钱|价格|价钱|"
    r"\bhow\s+much\b|\bprices?\b|\brates?\b|\bcosts?\b|\bcheapest\b",
    re.IGNORECASE,
)
# 讨价还价、折扣与最终成交价属于经营决定，仍交人工；单纯问参考价不再转人工。
_BARGAIN_PATTERN = re.compile(
    # 「打五折」「给我八折」「8.5折」也是要优惠（门禁 PI-折扣：原来只认「打折」，
    # 客人要五折没有转人工）。「折叠床」「折腾」前面不是数字，不会误中。
    r"优惠|打折|折扣|几折|打?[一二三四五六七八九\d](?:\.\d)?折(?![叠腾返])|"
    r"便宜点|便宜一点|便宜些|能不能便宜|可以便宜|砍价|讲价|少点|少一点|"
    r"最低能|底价|\bdiscounts?\b|\bcheaper\b|\bbest\s+price\b|\bdeal\b",
    re.IGNORECASE,
)

_HOMESTAY_RELATED_PATTERN = re.compile(
    r"民宿|住宿|房间|房源|房型|有房|入住|退房|续住|预订|订单|"
    r"价格|房价|退款|取消|改期|投诉|停车|门锁|密码|二维码|"
    r"WiFi|无线网|空调|热水|洗衣|投影|发票|行李|保洁|维修|"
    r"被子|枕头|矿泉水|纸巾|麻将|布置|武汉|黄鹤楼|东湖|"
    r"景点|旅游|路线|地铁|公交|打车|餐厅|咖啡|商场|医院|"
    r"药店|夜市|天气|确认无误|homestay|hotel|room|stay|check[ -]?in|"
    r"check[ -]?out|booking|reservation|refund|parking|wifi|"
    r"Wuhan|attraction|travel|restaurant|weather",
    re.IGNORECASE,
)

_CLEARLY_UNRELATED_PATTERN = re.compile(
    r"股票|基金|期货|量化交易|编程|写代码|算法题|操作系统|"
    r"政治评论|军事分析|游戏攻略|小说创作|"
    r"stock|trading|programming|source code|video game",
    re.IGNORECASE,
)

_SERVICE_REQUEST_PATTERN = re.compile(
    r"(?:请|帮|麻烦|需要|想要|能否|可以).{0,10}"
    r"(?:保洁|打扫|清洁|换洗|更换|维修|修理|补充|补|送|拿|加床|布置|接送)|"
    r"(?:保洁|打扫|清洁|换洗|更换|维修|修理|补充|补|送|拿|加床|布置|接送)"
    r".{0,10}(?:一下|一份|一个|一床|一点|一些|吗|么|吧|谢谢)|"
    r"(?:床单|被子|枕头|矿泉水|纸巾|毛巾|洗漱用品).{0,10}"
    r"(?:脏了|没有了|没了|不够|需要|补|换)|"
    r"(?:补|换|送|拿).{0,6}(?:床单|被子|枕头|矿泉水|纸巾|毛巾|洗漱用品)|"
    r"房间.{0,6}(?:没水|缺水|没有热水)|"
    r"(?:提前入住|延迟退房|晚点退房)|"
    r"(?:clean|repair|replace|bring|deliver|extra bed|early check[ -]?in|late check[ -]?out)",
    re.IGNORECASE,
)

_FACILITY_FAULT_SIGNAL = (
    r"坏了|故障|打不开|关不上|拉不动|按不动|不工作|不能用|用不了|没反应|"
    r"锁住|卡住|堵(?:住|了)|漏水|不亮(?:了)?|不制冷|不加热|异响|"
    r"出(?:了)?(?:点|一点)?问题|有(?:点|一点)?问题|不太正常|不正常|异常|"
    r"不对劲|连不上|断网|停电|没电|"
    r"(?:网络|Wi[ -]?Fi|无线网|连接|电源|供水).{0,6}断了|"
    r"(?:没(?:有)?|不出)热水|"
    r"broken|not\s+working|doesn'?t\s+work|can(?:not|'t)\s+use|"
    r"won'?t\s+start|can(?:not|'t)\s+connect|issue|problem|abnormal"
)
_FACILITY_FAULT_PATTERN = re.compile(
    _FACILITY_FAULT_SIGNAL,
    re.IGNORECASE | re.DOTALL,
)
_ABSTRACT_FAULT_TOPIC_PATTERN = re.compile(
    r"设计|方案|订单|价格|房价|回复|消息|态度|行程|计划|代码|程序|页面|"
    r"政策|规则|合同|账单|付款|退款|预订|服务|卫生|清洁|"
    r"身体|健康|(?<!不)工作|感情|情绪|这个事情|那个事情|"
    r"design|plan|order|price|reply|message|attitude|code|program|policy",
    re.IGNORECASE,
)
_HOMESTAY_FACILITY_CONTEXT_PATTERN = re.compile(
    r"房间|客房|民宿|店里|公区|公共区域|楼道|房门|卫生间|浴室|厨房|"
    r"room|homestay|property|guesthouse|public area",
    re.IGNORECASE,
)
_PRIVATE_FACILITY_PATTERN = re.compile(
    r"(?:我的|我自己的|个人的|私人的|我自己带来的|我带来的|自己带的|自带的)"
    r".{0,16}(?:手机|电脑|平板|相机|耳机|充电器|充电宝|行李箱|咖啡机|"
    r"吹风机|手表|汽车|电动车|自行车|车辆)|"
    r"(?:我自己带来|我带来|自己带来|自带)(?:的)?.{1,20}"
    rf"(?:{_FACILITY_FAULT_SIGNAL})",
    re.IGNORECASE | re.DOTALL,
)
_INHERENT_PRIVATE_FACILITY_PATTERN = re.compile(
    r"手机|笔记本电脑|平板|相机|耳机|充电宝|手表|汽车|电动车|自行车|车辆|"
    r"phone|laptop|tablet|camera|headphones|power bank|watch|car|bicycle",
    re.IGNORECASE,
)
_EXTERNAL_PLACE_PATTERN = re.compile(
    r"景区|商场|餐厅|饭店|咖啡店|便利店|地铁站?|火车站|机场|医院|学校|"
    r"公司|办公室|停车场|路边|街上|scenic area|mall|restaurant|station|airport",
    re.IGNORECASE,
)

# 住宿资料确认只更新已有订单快照，不能据此创建新的预订请求。
_BOOKING_CONFIRMATION_PATTERN = re.compile(
    r"(?:我要|帮我|请帮我|我想)(?:预订|订房|订这个房间)|please book|"
    r"确认预订|(?:就按|按这个|按以上|按上述).{0,6}(?:订|预订|提交)|"
    r"(?:submit|place).{0,12}(?:booking|reservation)",
    re.IGNORECASE,
)


def asks_stay_availability(text: str) -> bool:
    """判断客人是否在问能不能住、有没有房；这类事实只能来自百居易实时查询。"""
    return _STAY_INTENT_PATTERN.search(text) is not None


def is_transaction_sensitive(text: str) -> bool:
    """判断文本是否涉及不能依靠模型猜测的交易事实。"""
    return _TRANSACTION_PATTERN.search(text) is not None or asks_stay_availability(text)


def is_property_specific(text: str) -> bool:
    """判断文本是否要求回答本民宿专属事实。

    除固定词外，复用知识检索的主题别名：检索能用「泊车」「wash my clothes」
    找到审核知识，这里却判为非专属问题的话，回复里的本店信息会被当作未审核
    宣传删掉，召回等于白做。别名只收含义明确的说法，不把「猫」「lift」这类
    多义词算作在问本店。
    """
    return _PROPERTY_SPECIFIC_PATTERN.search(text) is not None or bool(detect_property_topics(text))


# 打听内部系统（用什么模型、知识库多少条、调哪个接口）：固定婉拒，不进入本店事实
# 判定。此前被「你们」判成本店问题，回「尚未确认民宿专属信息」（门禁 PI-内部）。
_INTERNAL_SYSTEM_PROBE = re.compile(
    r"(?:什么|哪个|哪家|哪种)(?:ai|大)?模型|模型是(?:什么|哪)|知识库|系统提示词?|提示词"
    r"|(?:调|用|走)的?(?:是)?(?:哪个|什么)(?:接口|api)|数据库"
    r"|\bwhat\s+(?:ai\s+|language\s+)?model\b|\bsystem\s+prompt\b|\bwhich\s+api\b"
    r"|\bknowledge\s+base\b",
    re.IGNORECASE,
)

# 措辞要能原样通过客人侧出口：「我都可以帮您」会被当成软承诺删掉（1.53.0 测试号
# 实测只剩前半句），所以用「欢迎直接问我」。
INTERNAL_SYSTEM_REPLY_ZH = "内部系统的信息不便透露。入住、房间或武汉出行方面的问题，欢迎直接问我。"
INTERNAL_SYSTEM_REPLY_EN = (
    "I can't share details about our internal systems. Feel free to ask me about your stay, "
    "the rooms, or getting around Wuhan."
)


def is_internal_system_probe(text: str) -> bool:
    """判断客人是否在打听机器人背后的模型、知识库或接口。"""
    return _INTERNAL_SYSTEM_PROBE.search(text) is not None


def handoff_reason(text: str) -> str | None:
    """按固定优先级识别必须由 YuMi 决策的高风险事项。"""
    if _BARGAIN_PATTERN.search(text) is not None:
        return "price"
    if is_policy_inquiry(text):
        return None
    for reason, pattern in _HIGH_RISK_PATTERNS:
        if reason != "early_check_in" and pattern.search(text) is not None:
            return reason
    if _BARGAIN_PATTERN.search(text) is not None:
        return "price"
    return None


def asks_room_price(text: str) -> bool:
    """判断客人是否在问房价；早餐、停车、洗衣这类服务收费由审核知识回答，不算。"""
    return _ROOM_PRICE_PATTERN.search(text) is not None and not is_static_service_fee(text)


def is_homestay_related(text: str) -> bool:
    """判断问题是否属于民宿住宿或武汉旅行助手范围。"""
    if _HOMESTAY_RELATED_PATTERN.search(text) is not None:
        return True
    # “好的”“两个人”等短承接语缺少主题词，继续交给会话上下文判断；
    # 只对明确落在其他专业领域的问题执行本地拒答。
    return _CLEARLY_UNRELATED_PATTERN.search(text) is None


def is_policy_inquiry(text: str) -> bool:
    """识别只读政策咨询；混合的明确申请仍按申请处理。"""
    inquiry = re.search(
        r"政策|规定|规则|条件|收费|费用|怎么收费|policy|rules?|conditions?|fee", text, re.I
    )
    action = re.search(
        r"帮我|给我|我想退|想退款|要退|申请|安排|现在需要|please.{0,15}(?:arrange|refund|book)",
        text,
        re.I,
    )
    return inquiry is not None and action is None


def is_service_request(text: str) -> bool:
    """判断本轮客人是否明确提出需要执行的民宿服务。"""
    if is_policy_inquiry(text):
        return False
    if has_facility_fault_signal(text):
        return facility_fault_exclusion(text) is None
    return _SERVICE_REQUEST_PATTERN.search(text) is not None


def has_facility_fault_signal(text: str) -> bool:
    """识别开放式设施故障表达，不依赖固定设备名称清单。"""
    normalized = " ".join(text.split())
    # 分句判断让“订单有问题，灯也坏了”只保留真实设施故障部分。
    return any(
        _FACILITY_FAULT_PATTERN.search(segment) is not None
        and _ABSTRACT_FAULT_TOPIC_PATTERN.search(segment) is None
        for segment in re.split(r"[，。！？!?；;]+", normalized)
        if segment.strip()
    )


def facility_fault_exclusion(text: str) -> Literal["private", "external"] | None:
    """返回明确的私人或外部故障归属；含糊短句仍按民宿设施理解。"""
    if not has_facility_fault_signal(text):
        return None
    if _PRIVATE_FACILITY_PATTERN.search(text) is not None:
        return "private"
    if (
        _INHERENT_PRIVATE_FACILITY_PATTERN.search(text) is not None
        and _HOMESTAY_FACILITY_CONTEXT_PATTERN.search(text) is None
    ):
        return "private"
    for place_match in _EXTERNAL_PLACE_PATTERN.finditer(text):
        start = max(0, place_match.start() - 12)
        end = min(len(text), place_match.end() + 24)
        nearby = text[start:end]
        if (
            _FACILITY_FAULT_PATTERN.search(nearby) is not None
            and _HOMESTAY_FACILITY_CONTEXT_PATTERN.search(nearby) is None
        ):
            return "external"
    return None


def is_booking_action_request(text: str) -> bool:
    """判断本轮客人是否明确确认提交预订资料，而非仅咨询预订。"""
    return _BOOKING_CONFIRMATION_PATTERN.search(text) is not None


def is_static_service_fee(text: str) -> bool:
    """识别可用审核知识回答的服务收费；明确订单、房价、退款仍保持交易边界。"""
    if _LODGING_PRICE_PATTERN.search(text) or asks_stay_availability(text) or re.search(
        r"房价|房费|订房|预订|订单"
        r"|退款|退费|退多少|取消|改期|支付|付款|到账|发票金额|availability|reschedule"
        r"|room\s+(?:rate|price)|reservation|booking|refund|cancel|payment|invoice\s+amount"
        r"|how\s+much.{0,45}\brooms?\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return (
        bool(detect_property_topics(text))
        and re.search(
            r"多少钱|收费|费用|价格|免费|how\s+much|\bcost|\bfee|\bprice|\bcharge|\bfree",
            text,
            re.IGNORECASE,
        )
        is not None
    )


def policy_variants(text: str) -> tuple[str, ...]:
    """确定性规则的候选文本：原文、跨消息空白归一化、去空白；不改变交给模型的原文。"""
    flattened = " ".join(text.split())
    compact = re.sub(r"\s+", "", text)
    return tuple(dict.fromkeys((text, flattened, compact)))


def agitated_cleared_by_plan(plan_outcome: PlanOutcome | None, text: str) -> bool:
    """情绪词是否被计划复核去除：规划成功、针对本轮正文且没有任何项标为投诉（Spec §2.6）。

    规划失败或摘要不一致时返回假，保留现行保守行为。
    """
    plan = usable_plan(plan_outcome, text)
    return plan is not None and "complaint" not in plan_risks(plan)


def resolve_handoff_reason(text: str, plan_outcome: PlanOutcome | None = None) -> str | None:
    """统一接管理由：各出口都调用它，情绪词复核结果不会在后续出口重新生效（V4-R1）。

    硬理由（退款、平台投诉、议价）保持确定性，不受计划影响；只有 agitated 可复核。
    """
    reason = next(
        (found for variant in policy_variants(text) if (found := handoff_reason(variant))),
        None,
    )
    if reason == "agitated" and agitated_cleared_by_plan(plan_outcome, text):
        return None
    return reason


@dataclass(frozen=True)
class TaskResolution:
    """统一任务写入口的结论（Spec §2.5）。

    - register：本轮是否登记任务；
    - task_type：多事项或订房意向为 SPECIAL_SERVICE，单个设施故障为维修，None 表示沿用
      模型建议的类型（缺省时为 SPECIAL_SERVICE）；
    - ask_confirm：不登记并请客人确认（规划失败、关联不明或意图不明）；
    - safety_tip：存在当前设施故障信号，需即时给安全提示，与是否建任务无关；
    - current_fault：计划判定存在当前设施故障；
    - withdrawn：本轮撤回的事项名称，用于通知管家（D11），不取消已有任务。
    """

    register: bool = False
    task_type: BusinessTaskType | None = None
    subjects: tuple[str, ...] = ()
    ask_confirm: bool = False
    safety_tip: bool = False
    current_fault: bool = False
    withdrawn: tuple[str, ...] = ()
    planned: bool = False


def _lexical_request(text: str) -> bool:
    """词面服务或订房信号；只用于规划失败时决定是否回确认话术，不能授权登记。"""
    return any(is_service_request(variant) for variant in policy_variants(text)) or (
        is_booking_action_request(text)
    )


def _lexical_facility(text: str) -> bool:
    """词面设施故障信号，排除私人物品与外部场所。"""
    return has_facility_fault_signal(text) and facility_fault_exclusion(text) is None


def resolve_task_request(plan_outcome: PlanOutcome | None, text: str) -> TaskResolution:
    """所有任务写入口共用的判定：相同计划与正文必得相同结论（V3-R5、V4-R3、V4-R5）。

    规划成功时按事项与原文顺序取最终状态：撤回通过 `withdraws` 指向它撤回的申请项，
    撤回在前且本计划内没有更早申请时表示对象不在本计划；有更早申请却关联不明、或
    撤回项位置失效时，不登记并转确认。规划失败时服务类不新建任务，词面命中只回确认
    话术（D13）；设施有故障信号时给安全提示并请客人确认（D14）。
    """
    plan = usable_plan(plan_outcome, text)
    if plan is None:
        if _lexical_facility(text):
            return TaskResolution(ask_confirm=True, safety_tip=True)
        return TaskResolution(ask_confirm=_lexical_request(text))
    assert plan.plan is not None
    items = plan.plan.items
    requests_all = [item for item in items if item.kind in REQUEST_KINDS]
    requests = [item for item in plan.plan.valid_items if item.kind in REQUEST_KINDS]
    withdraws = [item for item in items if item.kind == "request_withdraw"]
    safety_tip = any(item.kind == "facility_fault" for item in requests)
    confirm = TaskResolution(ask_confirm=True, safety_tip=safety_tip, planned=True)
    # 失效撤回无法判断先后：同轮有任何申请项就转确认；有撤回时失效申请同理。
    if requests_all and any(not item.valid for item in withdraws):
        return confirm
    if withdraws and any(not item.valid for item in requests_all):
        return confirm
    by_id = {item.id: item for item in requests}
    cancelled: set[int] = set()
    withdrawn: list[str] = []
    for withdraw in (item for item in withdraws if item.valid):
        if withdraw.withdraws is not None:
            target = by_id.get(withdraw.withdraws)
            if target is None or target.start >= withdraw.start:
                return confirm
            cancelled.add(target.id)
            withdrawn.append(target.subject or withdraw.subject)
        elif any(item.start < withdraw.start for item in requests):
            # 本计划内有更早申请，却没给出撤回对象：无法可靠关联。
            return confirm
        else:
            withdrawn.append(withdraw.subject)
    active = [item for item in requests if item.id not in cancelled]
    if not active:
        unclear = any(item.kind == "unclear" for item in plan.plan.valid_items)
        return TaskResolution(
            # 只有不明确项（Spec §2.5 表末行）：不登记；词面像申请时请客人确认（D11 针对「意图
            # 不明确的请求」）。门禁实测：信息问题被标为不明确时，回「需要安排什么」答非所问。
            ask_confirm=unclear and not withdraws and _lexical_request(text),
            safety_tip=safety_tip,
            withdrawn=tuple(filter(None, withdrawn)),
            planned=True,
        )
    facility = [item for item in active if item.kind == "facility_fault"]
    if len(active) > 1 or any(item.kind == "booking_request" for item in active):
        task_type: BusinessTaskType | None = BusinessTaskType.SPECIAL_SERVICE
    elif facility:
        task_type = BusinessTaskType.MAINTENANCE
    else:
        task_type = None
    return TaskResolution(
        register=True,
        task_type=task_type,
        subjects=tuple(item.subject for item in active if item.subject),
        safety_tip=safety_tip,
        current_fault=bool(facility),
        withdrawn=tuple(filter(None, withdrawn)),
        planned=True,
    )


TASK_CONFIRM_REPLY_ZH = "请问需要我们现在为您安排什么？确认后我马上登记。"
TASK_CONFIRM_REPLY_EN = (
    "What would you like us to arrange for you now? Once you confirm, I'll register it right away."
)
FACILITY_CONFIRM_REPLY_ZH = "如需安排维修，请回复确认，确认后我马上登记。"
FACILITY_CONFIRM_REPLY_EN = (
    "If you'd like us to arrange a repair, please reply to confirm and I'll register it right away."
)
