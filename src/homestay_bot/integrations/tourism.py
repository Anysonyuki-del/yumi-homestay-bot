import logging
import re
from datetime import date
from typing import Literal

logger = logging.getLogger(__name__)

WebSearchStatus = Literal["unknown", "ok", "unsupported", "degraded"]
TourismQueryMode = Literal["none", "stable", "live"]
TourismReplyCategory = Literal["weather", "event", "ticket", "tourism"]
TourismReplyLanguage = Literal["zh", "en"]

_STABLE_TOURISM_PATTERN = re.compile(
    r"景点|好玩|游玩|旅游|一日游|半日游|攻略|美食|小吃|餐厅|"
    r"玩啥|玩什么|去哪玩|"
    r"attraction|sightseeing|itinerary|food|restaurant|where to (?:go|visit)",
    re.IGNORECASE,
)
_LIVE_TOURISM_PATTERN = re.compile(
    r"展览|演出|活动|音乐会|演唱会|门票|票价|开放时间|营业时间|"
    r"几点(?:开门|关门|开放|闭馆)|开到几点|开门吗|关门吗|营业吗|开放吗|"
    r"怎么去|路线|地铁|公交|打车|距离|多远|远吗|公里|路程|要多久|怎么到|"
    r"天气|封路|闭馆|堵不堵|路况|交通状况|"
    r"exhibition|show|event|concert|ticket|opening hours|how to get|"
    r"weather|distance|route|real[ -]?time transit|"
    r"admission (?:fees?|prices?)|book admission",
    re.IGNORECASE,
)
_TOURISM_BOOKING_OVERRIDE_PATTERN = re.compile(
    r"门票|票务|景区|景点|展览|演出|活动|音乐会|演唱会|"
    r"tickets?|admission|concert|show|event|attraction",
    re.IGNORECASE,
)
_DATED_TOURISM_PATTERN = re.compile(
    r"(?:今天|今晚|明天|明日|后天|本周|这个周末|周末).*?(?:去哪|玩|游)|"
    r"(?:today|tonight|tomorrow|this week|this weekend).*?"
    r"(?:go|visit|play|tour)",
    re.IGNORECASE,
)
_BOOKING_PATTERN = re.compile(
    r"有房|房态|订房|预订|入住|退房|房间价格|房价|"
    r"availability|book|booking|check[- ]?in|check[- ]?out|room rate",
    re.IGNORECASE,
)
_LODGING_OBJECT_PATTERN = re.compile(
    r"房间|房源|房型|民宿|酒店|客房|住宿|"
    r"room|property|homestay|hotel|accommodation",
    re.IGNORECASE,
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
_BARE_URL_PATTERN = re.compile(
    r"https?://\S+?"
    r"(?=(?:[。，；！？）)]|\.(?=\s|$|[A-Z])|[,;!?](?=\s|$)|\s|$))"
)
_DOMAIN_PATTERN = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_NATURAL_FOOTER_PATTERN = re.compile(
    r"(?:"
    r"这是我今天（\d{1,2}月\d{1,2}日）帮您查到的"
    r"(?:最新预报|最新天气信息|最新活动信息|最新票务与开放信息|最新出行信息)"
    # 来源子句连同它前面的逗号一起可选：v1.28.0 起不再列举来源，句读也随之从
    # 「最新预报，主要参考了X等公开信息。」变成「最新预报。」。库里存量正文仍是
    # 旧格式，而改写器读的正是这些存量消息，切不出页脚会把收尾当正文送去精炼。
    r"(?:，主要参考了[^。\n]+等公开信息)?。"
    r"(?:天气可能临时变化，出门前可以再看一眼实时情况。|"
    r"活动安排可能临时调整，出发前可以再确认一下。|"
    r"票价和开放安排可能临时调整，出发前可以再确认一下。|"
    r"出行信息可能临时变化，出发前可以再确认一下。)"
    r"|"
    r"I checked this latest (?:forecast|event information|ticket and opening information|"
    r"travel information) for you today \([A-Z][a-z]+ \d{1,2}\)"
    r"(?:, mainly using public information from .+?)?\. "
    r"(?:Weather can change at short notice, so please check the live forecast once "
    r"more before heading out\.|Event schedules can change, so please confirm once "
    r"more before heading out\.|Prices and opening arrangements can change, so please "
    r"confirm once more before heading out\.|Travel information can change, so please "
    r"check once more before heading out\.)"
    r")$",
    re.IGNORECASE,
)


class TourismSearchError(RuntimeError):
    """表示旅游联网请求无法生成带来源的可靠答复。"""

    def __init__(self, status: WebSearchStatus) -> None:
        """保存可公开给健康检查的非敏感失败分类。"""
        super().__init__(status)
        self.status = status


class WebSearchState:
    """在进程内保存最近一次联网能力状态。"""

    def __init__(self) -> None:
        """首次真实联网前使用 unknown，避免伪报可用。"""
        self._status: WebSearchStatus = "unknown"

    def get(self) -> WebSearchStatus:
        """返回最近一次联网能力状态。"""
        return self._status

    def set(self, status: WebSearchStatus) -> None:
        """只保存枚举内状态，不记录问题或搜索正文。"""
        self._status = status


def latest_user_question(messages: list[dict[str, str]]) -> dict[str, str]:
    """只返回最后一条客人文本，隔离历史中的个人资料。"""
    for message in reversed(messages):
        if message.get("role") == "user":
            return {"role": "user", "content": message.get("content", "")}
    return {"role": "user", "content": ""}


def classify_tourism_query(
    messages: list[dict[str, str]],
) -> TourismQueryMode:
    """按信息时效分类旅游问题，并始终让预订查询优先。"""
    content = latest_user_question(messages)["content"]
    if _BOOKING_PATTERN.search(content):
        if _LODGING_OBJECT_PATTERN.search(content):
            return "none"
        # “预订门票/演出”属于旅游时效信息，不能被民宿预订关键词截走。
        if _TOURISM_BOOKING_OVERRIDE_PATTERN.search(content):
            return "live"
        return "none"
    if _LIVE_TOURISM_PATTERN.search(content) or _DATED_TOURISM_PATTERN.search(content):
        return "live"
    if _STABLE_TOURISM_PATTERN.search(content):
        return "stable"
    return "none"


def is_tourism_query(messages: list[dict[str, str]]) -> bool:
    """兼容既有调用：稳定或实时旅游问题均返回真。"""
    return classify_tourism_query(messages) != "none"


def _plain_text_tourism_body(content: str) -> str:
    """只移除明确 Markdown 结构，保留正文中的普通星号与下划线。"""
    cleaned = _MARKDOWN_LINK_PATTERN.sub(r"\1", content)
    cleaned = _BARE_URL_PATTERN.sub("", cleaned)
    cleaned = _DOMAIN_PATTERN.sub("", cleaned)
    cleaned = re.sub(
        r"(?i)\s*[（(]?\s*(?:查询日期|query date)\s*[:：]\s*"
        r"[^,，;；。.!?\n）)]*[）)]?",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\s*[（(]?\s*(?:参考来源|sources?)\s*[:：]\s*"
        r"[^;；。.!?）)\n]*[）)]?[;；。.!?]?",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    cleaned = re.sub(r"[ \t]*[,，;；]+[ \t]*(?=\n|$)", "", cleaned)
    cleaned = re.sub(r"\*{3}(?=\S)([^\n*]*?\S)\*{3}", r"\1", cleaned)
    cleaned = re.sub(r"_{3}(?=\S)([^\n_]*?\S)_{3}", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^\n*]+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^\n_]+?)__", r"\1", cleaned)
    cleaned = re.sub(r"\*(?=\S)([^\n*]*?\S)\*", r"\1", cleaned)
    cleaned = re.sub(r"_(?=\S)([^\n_]*?\S)_", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", cleaned)
    return cleaned.rstrip()


def split_tourism_reply(reply_text: str) -> tuple[str, str]:
    """拆出本地生成的自然证据收尾，供模型只精炼旅游正文。"""
    match = _NATURAL_FOOTER_PATTERN.search(reply_text)
    if match is not None:
        body = reply_text[: match.start()]
        return body.rstrip(), match.group(0).strip()
    return reply_text.rstrip(), ""


def _natural_evidence_footer(
    *,
    queried_on: date,
    language: TourismReplyLanguage,
    category: TourismReplyCategory,
) -> str:
    """把已校验的查询日期转换为民宿管家式自然收尾。

    只声明「今天查到的」和「可能临时变化」，不列举来源名。列举来源本意是诚实标注
    依据，实际拿到的却是搜索结果的网页标题；六条真实样本显示带这句的三条全部被
    企业微信以安全限制拦下、不带的三条全部送达，而兜底重发会把「可能临时变化」这
    句提醒一起丢掉——坚持列举来源反而让客人连时效提示都收不到。

    诚实性由「我今天帮您查到的」加「可能临时变化」承担；调用方已保证确有搜索结果
    且陈旧来源已被剔除，因此这句话本身仍然为真。
    """
    if language == "en":
        category_name, caution = {
            "weather": (
                "forecast",
                "Weather can change at short notice, so please check the live "
                "forecast once more before heading out.",
            ),
            "event": (
                "event information",
                "Event schedules can change, so please confirm once more before "
                "heading out.",
            ),
            "ticket": (
                "ticket and opening information",
                "Prices and opening arrangements can change, so please confirm "
                "once more before heading out.",
            ),
            "tourism": (
                "travel information",
                "Travel information can change, so please check once more before "
                "heading out.",
            ),
        }[category]
        display_date = f"{queried_on.strftime('%B')} {queried_on.day}"
        return (
            f"I checked this latest {category_name} for you today "
            f"({display_date}). {caution}"
        )

    category_name, caution = {
        "weather": ("最新预报", "天气可能临时变化，出门前可以再看一眼实时情况。"),
        "event": ("最新活动信息", "活动安排可能临时调整，出发前可以再确认一下。"),
        "ticket": (
            "最新票务与开放信息",
            "票价和开放安排可能临时调整，出发前可以再确认一下。",
        ),
        "tourism": ("最新出行信息", "出行信息可能临时变化，出发前可以再确认一下。"),
    }[category]
    return (
        f"这是我今天（{queried_on.month}月{queried_on.day}日）帮您查到的"
        f"{category_name}。{caution}"
    )


def format_tourism_reply(
    reply_text: str,
    citations: list[tuple[str, str]],
    queried_on: date,
    *,
    language: TourismReplyLanguage = "zh",
    category: TourismReplyCategory = "tourism",
) -> str:
    """生成无链接、带自然时效依据的客人可见旅游纯文本。"""
    body, _existing_footer = split_tourism_reply(reply_text)
    clean_reply = _plain_text_tourism_body(body)
    if not clean_reply:
        raise ValueError("联网结果没有可读回复正文")
    # 仍然要求确有搜索结果：可以不点名来源，但不能在没查到任何东西时说「我查到的」。
    if not citations:
        raise ValueError("联网结果没有搜索来源")
    footer = _natural_evidence_footer(
        queried_on=queried_on,
        language=language,
        category=category,
    )
    return f"{clean_reply}\n\n{footer}"
