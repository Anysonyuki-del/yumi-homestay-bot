import re

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
