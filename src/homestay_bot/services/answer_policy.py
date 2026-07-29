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


def is_transaction_sensitive(text: str) -> bool:
    """判断文本是否涉及不能依靠模型猜测的交易事实。"""
    return _TRANSACTION_PATTERN.search(text) is not None


def is_property_specific(text: str) -> bool:
    """判断文本是否要求回答本民宿专属事实。"""
    return _PROPERTY_SPECIFIC_PATTERN.search(text) is not None
