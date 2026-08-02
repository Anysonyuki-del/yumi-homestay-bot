import re
from datetime import date
from typing import Literal
from urllib.parse import urlparse

WebSearchStatus = Literal["unknown", "ok", "unsupported", "degraded"]

_TOURISM_PATTERN = re.compile(
    r"景点|好玩|游玩|旅游|一日游|半日游|攻略|美食|小吃|餐厅|"
    r"展览|演出|活动|门票|票价|开放时间|营业时间|怎么去|路线|"
    r"地铁|公交|打车|距离|多远|公里|路程|怎么到|天气.*(?:玩|游)|"
    r"attraction|sightseeing|itinerary|food|restaurant|exhibition|"
    r"show|event|ticket|opening hours|how to get",
    re.IGNORECASE,
)
_BOOKING_PATTERN = re.compile(
    r"有房|房态|订房|预订|入住|退房|房间价格|房价|"
    r"availability|book|booking|check[- ]?in|check[- ]?out|room rate",
    re.IGNORECASE,
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
_BARE_URL_PATTERN = re.compile(r"https?://\S+")
_OFFICIAL_SOURCE_NAMES = {
    "www.wuhan.gov.cn": "武汉市人民政府",
    "3g.wuhan.gov.cn": "武汉市人民政府",
    "wlj.wuhan.gov.cn": "武汉市文化和旅游局",
    "ylj.wuhan.gov.cn": "武汉市园林和林业局",
    "fgw.wuhan.gov.cn": "武汉市发展和改革委员会",
    "hbj.wuhan.gov.cn": "武汉市生态环境局",
    "gaj.wuhan.gov.cn": "武汉市公安局",
    "zrzyhgh.wuhan.gov.cn": "武汉市自然资源和城乡建设局",
}


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


def is_tourism_query(messages: list[dict[str, str]]) -> bool:
    """以预订优先规则识别需要实时搜索的旅游问题。"""
    content = latest_user_question(messages)["content"]
    if _BOOKING_PATTERN.search(content):
        return False
    return _TOURISM_PATTERN.search(content) is not None


def _source_display_name(title: str, url: str) -> str:
    """把网址型标题转换为不会自动链接的来源名称。"""
    hostname = urlparse(url).netloc.lower()
    normalized_title = title.strip()
    if normalized_title and normalized_title not in {url, hostname}:
        return _BARE_URL_PATTERN.sub("", normalized_title).strip()
    return _OFFICIAL_SOURCE_NAMES.get(hostname, hostname.replace(".", "·"))


def format_tourism_reply(
    reply_text: str,
    citations: list[tuple[str, str]],
    queried_on: date,
) -> str:
    """移除模型生成的链接，并追加日期和最多五个来源名称。"""
    clean_reply = _MARKDOWN_LINK_PATTERN.sub(r"\1", reply_text)
    clean_reply = _BARE_URL_PATTERN.sub("", clean_reply).rstrip()
    source_names = list(
        dict.fromkeys(
            _source_display_name(title, url)
            for title, url in citations
        )
    )[:5]
    return (
        f"{clean_reply}\n\n查询日期：{queried_on.isoformat()}\n"
        f"参考来源：{'、'.join(source_names)}"
    )
