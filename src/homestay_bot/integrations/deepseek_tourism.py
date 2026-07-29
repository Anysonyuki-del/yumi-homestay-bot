import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.tourism import (
    TourismSearchError,
    WebSearchStatus,
    format_tourism_reply,
)

_RECENT_PATTERN = re.compile(
    r"最近|近期|本周|本月|今天|明天|"
    r"recent|upcoming|this week|this month|today|tomorrow",
    re.IGNORECASE,
)
_EVENT_PATTERN = re.compile(
    r"演出|活动|展览|音乐会|演唱会|话剧|相声|戏曲|音乐节|"
    r"show|event|exhibition|concert|musical|opera|festival",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_CHINESE_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})年"
    r"(?P<month>\d{1,2})月"
    r"(?P<day>\d{1,2})日"
)
_ISO_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})-"
    r"(?P<month>\d{2})-"
    r"(?P<day>\d{2})"
)
_MONTH_DAY_PATTERN = re.compile(r"\d{1,2}月\d{1,2}日")
_MONTH_ONLY_PATTERN = re.compile(r"\d{1,2}月(?!\d{1,2}日)")
_YEAR_MONTH_PATTERN = re.compile(
    r"(?P<year>20\d{2})年(?P<month>\d{1,2})月"
)


class DeepSeekTourismSearcher:
    """通过 DeepSeek Anthropic 原生 Web Search 回答武汉旅游问题。"""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        status_setter: Callable[[WebSearchStatus], None] | None = None,
    ) -> None:
        """注入 Anthropic 兼容客户端、模型和健康状态写入器。"""
        self._client = client
        self._model = model
        self._status_setter = status_setter

    def _set_status(self, status: WebSearchStatus) -> None:
        """更新非敏感搜索能力状态。"""
        if self._status_setter is not None:
            self._status_setter(status)

    @staticmethod
    def _extract_content(
        response: Any,
    ) -> tuple[str, list[tuple[str, str]]]:
        """从 Anthropic 内容块提取正文和搜索来源。"""
        text_parts: list[str] = []
        citations: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                text = str(getattr(block, "text", "")).strip()
                if text:
                    text_parts.append(text)
            if getattr(block, "type", None) != "web_search_tool_result":
                continue
            for result in getattr(block, "content", []) or []:
                url = str(getattr(result, "url", "")).strip()
                title = str(getattr(result, "title", "")).strip()
                if url and url not in seen_urls:
                    citations.append((title or url, url))
                    seen_urls.add(url)
        return "\n".join(text_parts), citations

    @staticmethod
    def _is_recent_event_question(question: str) -> bool:
        """识别必须执行日期与半月窗口校验的近期活动问题。"""
        return (
            _RECENT_PATTERN.search(question) is not None
            and _EVENT_PATTERN.search(question) is not None
        )

    @staticmethod
    def _remove_stale_citations(
        citations: list[tuple[str, str]],
        *,
        current_year: int,
    ) -> list[tuple[str, str]]:
        """删除标题中年份全部早于当前年份的明确过期来源。"""
        current: list[tuple[str, str]] = []
        for title, url in citations:
            years = [int(year) for year in _YEAR_PATTERN.findall(title)]
            if years and max(years) < current_year:
                continue
            current.append((title, url))
        return current

    @staticmethod
    def _has_valid_recent_event_dates(
        text: str,
        *,
        queried_on: date,
        priority_end: date,
        allow_month_ranges: bool,
    ) -> bool:
        """验证演出年份完整，且优先窗口内至少有一个可核验日期。"""
        allowed_years = {queried_on.year, priority_end.year}
        for line in text.splitlines():
            if _MONTH_DAY_PATTERN.search(line) and not any(
                str(year) in line for year in allowed_years
            ):
                return False
        mentioned_years = {
            int(year) for year in _YEAR_PATTERN.findall(text)
        }
        if any(year < queried_on.year for year in mentioned_years):
            return False

        event_dates: list[date] = []
        for pattern in (_CHINESE_DATE_PATTERN, _ISO_DATE_PATTERN):
            for match in pattern.finditer(text):
                try:
                    event_dates.append(
                        date(
                            int(match.group("year")),
                            int(match.group("month")),
                            int(match.group("day")),
                        )
                    )
                except ValueError:
                    return False
        if any(queried_on <= item <= priority_end for item in event_dates):
            return True
        if allow_month_ranges:
            allowed_months = {
                (queried_on.year, queried_on.month),
                (priority_end.year, priority_end.month),
            }
            for line in text.splitlines():
                if re.search(r"至|—|–|持续", line) is None:
                    continue
                line_months = {
                    (
                        int(match.group("year")),
                        int(match.group("month")),
                    )
                    for match in _YEAR_MONTH_PATTERN.finditer(line)
                }
                if line_months & allowed_months:
                    return True
        later_is_labeled = re.search(
            r"半个月后|15天后|优先窗口后|"
            r"after (?:the )?(?:15-day|priority) window|after 15 days",
            text,
            re.IGNORECASE,
        )
        return bool(event_dates and later_is_labeled)

    @staticmethod
    def _fill_window_date_years(
        text: str,
        *,
        queried_on: date,
        priority_end: date,
    ) -> str:
        """根据跨年窗口为省略年份的中文月日补全明确年份。"""

        def replace_date(match: re.Match[str]) -> str:
            """只补全前方没有四位年份的月日。"""
            prefix = text[max(0, match.start() - 5) : match.start()]
            if re.search(r"20\d{2}年$", prefix):
                return match.group(0)
            month = int(match.group(0).split("月", 1)[0])
            year = queried_on.year
            if (
                priority_end.year > queried_on.year
                and month <= priority_end.month
            ):
                year = priority_end.year
            return f"{year}年{match.group(0)}"

        filled_dates = _MONTH_DAY_PATTERN.sub(replace_date, text)

        def replace_month(match: re.Match[str]) -> str:
            """为展期中的省略年份月份补全窗口年份。"""
            prefix = filled_dates[
                max(0, match.start() - 5) : match.start()
            ]
            if re.search(r"20\d{2}年$", prefix):
                return match.group(0)
            month = int(match.group(0).removesuffix("月"))
            year = queried_on.year
            if (
                priority_end.year > queried_on.year
                and month <= priority_end.month
            ):
                year = priority_end.year
            return f"{year}年{match.group(0)}"

        return _MONTH_ONLY_PATTERN.sub(replace_month, filled_dates)

    async def search(
        self,
        *,
        question: str,
        language: Language,
        queried_on: date,
    ) -> str:
        """执行有限武汉搜索，要求正文和搜索证据同时存在。"""
        priority_end = queried_on + timedelta(days=15)
        system = (
            "你是武汉民宿的旅游客服。必须使用 Web Search 的真实结果回答，"
            "优先武汉政府、文旅局、景区、场馆和主办方来源。"
            f"当前日期：{queried_on.isoformat()}。"
            f"优先时间窗口：{queried_on.isoformat()} 至 "
            f"{priority_end.isoformat()}。"
            "近期问题优先展示该窗口内尚未结束的项目；不足时才补充更晚项目，"
            "并明确标注“半个月后”。不得把已经结束的活动当作近期推荐。"
            "每项活动日期必须注明完整年份。"
            "简单推荐给出3至5项，规划问题给出半日或一日路线。"
            "不要在正文中输出网址或 Markdown 链接。"
            if language is Language.ZH
            else (
                "You are a Wuhan homestay travel assistant. Use Web Search "
                "evidence, prefer official sources, and do not include URLs. "
                f"Current date: {queried_on.isoformat()}. Priority window: "
                f"{queried_on.isoformat()} through {priority_end.isoformat()}. "
                "For recent requests, prioritize events that have not ended "
                "within this window, include the full year for every event "
                "date, and label later events clearly."
            )
        )
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1800,
                system=system,
                messages=[{"role": "user", "content": question}],
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 2,
                        "user_location": {
                            "type": "approximate",
                            "country": "CN",
                            "city": "Wuhan",
                            "region": "Hubei",
                        },
                    }
                ],
            )
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            status: WebSearchStatus = (
                "unsupported" if status_code in {400, 404, 422} else "degraded"
            )
            self._set_status(status)
            raise TourismSearchError(status) from error

        text, citations = self._extract_content(response)
        if not text or not citations:
            self._set_status("degraded")
            raise TourismSearchError("degraded")
        if self._is_recent_event_question(question):
            citations = self._remove_stale_citations(
                citations,
                current_year=queried_on.year,
            )
            text = self._fill_window_date_years(
                text,
                queried_on=queried_on,
                priority_end=priority_end,
            )
            if not citations or not self._has_valid_recent_event_dates(
                text,
                queried_on=queried_on,
                priority_end=priority_end,
                allow_month_ranges=(
                    re.search(
                        r"展览|展会|exhibition",
                        question,
                        re.IGNORECASE,
                    )
                    is not None
                ),
            ):
                self._set_status("degraded")
                raise TourismSearchError("degraded")
        self._set_status("ok")
        return format_tourism_reply(text, citations, queried_on)
