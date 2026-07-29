import re
from datetime import date
from typing import Any, Literal

WebSearchStatus = Literal["unknown", "ok", "unsupported", "degraded"]

_TOURISM_PATTERN = re.compile(
    r"景点|好玩|游玩|旅游|一日游|半日游|攻略|美食|小吃|餐厅|"
    r"展览|演出|活动|门票|票价|开放时间|营业时间|怎么去|路线|"
    r"地铁|公交|打车|天气.*(?:玩|游)|"
    r"attraction|sightseeing|itinerary|food|restaurant|exhibition|"
    r"show|event|ticket|opening hours|how to get",
    re.IGNORECASE,
)
_BOOKING_PATTERN = re.compile(
    r"有房|房态|订房|预订|入住|退房|房间价格|房价|"
    r"availability|book|booking|check[- ]?in|check[- ]?out|room rate",
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


def is_tourism_query(messages: list[dict[str, str]]) -> bool:
    """以预订优先规则识别需要实时搜索的旅游问题。"""
    content = latest_user_question(messages)["content"]
    if _BOOKING_PATTERN.search(content):
        return False
    return _TOURISM_PATTERN.search(content) is not None


def web_search_tool() -> dict[str, Any]:
    """返回 Fenno/OpenAI Responses 兼容的武汉联网工具定义。"""
    return {
        "type": "web_search",
        "search_context_size": "low",
        "user_location": {
            "type": "approximate",
            "country": "CN",
            "city": "Wuhan",
            "region": "Hubei",
        },
    }


def extract_url_citations(response: Any) -> list[tuple[str, str]]:
    """从 Responses 消息注解提取并按 URL 去重来源。"""
    citations: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for output_item in getattr(response, "output", []):
        if getattr(output_item, "type", None) != "message":
            continue
        for content_item in getattr(output_item, "content", []):
            for annotation in getattr(content_item, "annotations", []):
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                nested = getattr(annotation, "url_citation", annotation)
                url = str(getattr(nested, "url", "")).strip()
                title = str(getattr(nested, "title", "")).strip() or url
                if url and url not in seen_urls:
                    citations.append((title, url))
                    seen_urls.add(url)
    return citations


def append_citations(
    reply_text: str,
    citations: list[tuple[str, str]],
    queried_on: date,
) -> str:
    """把查询日期和可点击来源追加到企业微信文本。"""
    source_lines = [
        f"{index}. {title}\n{url}"
        for index, (title, url) in enumerate(citations, start=1)
    ]
    return (
        f"{reply_text.rstrip()}\n\n查询日期：{queried_on.isoformat()}\n"
        f"来源：\n" + "\n".join(source_lines)
    )
