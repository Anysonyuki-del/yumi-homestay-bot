import re
from typing import Literal

_TRANSACTION_PATTERN = re.compile(
    r"房态|有房|可订|价格|房价|多少钱|参考价|退款|退多少|"
    r"取消|改期|付款|支付|到账|订单|预订状态|发票金额|"
    r"availability|room rate|price|refund|cancel|reschedule|"
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
        re.compile(r"退款|退钱|退费|refund", re.IGNORECASE),
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

_BOOKING_CONFIRMATION_PATTERN = re.compile(
    r"(?:以上|上述|这些|预订|入住|订单).{0,10}(?:资料|信息|内容|日期)?"
    r".{0,6}(?:确认无误|都对|没问题|可以提交|确认预订)|"
    r"(?:确认无误|资料无误|信息无误)|"
    r"(?:就按|按这个|按以上|按上述).{0,6}(?:订|预订|提交)|"
    r"(?:confirm|confirmed).{0,12}(?:booking|reservation|details)|"
    r"(?:booking|reservation).{0,12}(?:confirm|confirmed)",
    re.IGNORECASE,
)


def is_transaction_sensitive(text: str) -> bool:
    """判断文本是否涉及不能依靠模型猜测的交易事实。"""
    return _TRANSACTION_PATTERN.search(text) is not None


def is_property_specific(text: str) -> bool:
    """判断文本是否要求回答本民宿专属事实。"""
    return _PROPERTY_SPECIFIC_PATTERN.search(text) is not None


def handoff_reason(text: str) -> str | None:
    """按固定优先级识别必须由 YuMi 决策的高风险事项。"""
    for reason, pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(text) is not None:
            return reason
    if _LODGING_PRICE_PATTERN.search(text) is not None:
        return "price"
    return None


def is_homestay_related(text: str) -> bool:
    """判断问题是否属于民宿住宿或武汉旅行助手范围。"""
    if _HOMESTAY_RELATED_PATTERN.search(text) is not None:
        return True
    # “好的”“两个人”等短承接语缺少主题词，继续交给会话上下文判断；
    # 只对明确落在其他专业领域的问题执行本地拒答。
    return _CLEARLY_UNRELATED_PATTERN.search(text) is None


def is_service_request(text: str) -> bool:
    """判断本轮客人是否明确提出需要执行的民宿服务。"""
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
