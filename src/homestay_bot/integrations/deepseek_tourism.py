import logging
import re
from collections.abc import Callable
from datetime import date, timedelta
from time import monotonic
from typing import Any

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.tourism import (
    TourismReplyCategory,
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
_DISTANCE_PATTERN = re.compile(
    r"距离|多远|公里|路程|怎么到|how far|distance|kilometer|km",
    re.IGNORECASE,
)
_WEATHER_PATTERN = re.compile(
    r"天气|气温|温度|降雨|下雨|weather|forecast|temperature|rain",
    re.IGNORECASE,
)
_TICKET_PATTERN = re.compile(
    r"门票|票价|预订|开放时间|营业时间|开门|关门|闭馆|"
    r"\btickets?\b|\bticket prices?\b|\badmission (?:fees?|prices?)\b|"
    r"\bbook admission\b|\bopening hours?\b|\bclosing hours?\b",
    re.IGNORECASE,
)
_EXPLICIT_LOCATION_PATTERN = re.compile(
    r"武汉|武昌|汉口|北京|上海|广州|深圳|杭州|南京|成都|重庆|西安|长沙|"
    r"三亚|厦门|青岛|天津|苏州|郑州|合肥|南昌|福州|昆明|贵阳|海口|"
    r"大连|济南|太原|石家庄|沈阳|长春|哈尔滨|兰州|乌鲁木齐|拉萨|"
    r"香港|澳门|台北|Wuhan|Beijing|Shanghai|Guangzhou|Shenzhen|"
    r"(?:[\u4e00-\u9fff]{2,8})(?:市|省|自治区|县)|"
    r"\b(?:in|at|near|for)\s+[A-Z][A-Za-z -]{2,40}\b",
    re.IGNORECASE,
)
_TOMORROW_PATTERN = re.compile(r"明天|明日|tomorrow", re.IGNORECASE)
_DAY_AFTER_TOMORROW_PATTERN = re.compile(r"后天|day after tomorrow", re.IGNORECASE)
_TODAY_PATTERN = re.compile(r"今天|今日|today", re.IGNORECASE)
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
logger = logging.getLogger(__name__)

TourismCacheKey = tuple[str, str, date]
TourismCacheValue = tuple[float, str]


class DeepSeekTourismSearcher:
    """通过 DeepSeek Anthropic 原生 Web Search 回答武汉旅游问题。"""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        status_setter: Callable[[WebSearchStatus], None] | None = None,
        clock: Callable[[], float] = monotonic,
        cache_ttl_seconds: float = 600.0,
        cache_max_entries: int = 100,
    ) -> None:
        """注入搜索客户端、健康状态写入器和有界缓存参数。"""
        if cache_max_entries <= 0:
            raise ValueError("cache_max_entries 必须大于零")
        self._client = client
        self._model = model
        self._status_setter = status_setter
        self._clock = clock
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._cache: dict[TourismCacheKey, TourismCacheValue] = {}

    def _set_status(self, status: WebSearchStatus) -> None:
        """更新非敏感搜索能力状态。"""
        if self._status_setter is not None:
            self._status_setter(status)

    @staticmethod
    def _reply_category(question: str) -> TourismReplyCategory:
        """按客人问题选择自然证据收尾，不改变搜索意图与证据校验。"""
        if _WEATHER_PATTERN.search(question):
            return "weather"
        if _TICKET_PATTERN.search(question):
            return "ticket"
        if _EVENT_PATTERN.search(question):
            return "event"
        return "tourism"

    @staticmethod
    def _cache_key(
        question: str,
        language: Language,
        queried_on: date,
    ) -> TourismCacheKey:
        """生成不含客人身份的稳定缓存键，并统一空白与大小写。"""
        normalized_question = " ".join(question.split()).casefold()
        return normalized_question, language.value, queried_on

    def _get_cached_reply(self, key: TourismCacheKey) -> str | None:
        """返回未过期的已校验回复，并及时删除过期项。"""
        cached = self._cache.get(key)
        if cached is None:
            return None
        expires_at, reply = cached
        if self._clock() >= expires_at:
            self._cache.pop(key, None)
            return None
        return reply

    def _store_cached_reply(
        self,
        key: TourismCacheKey,
        reply: str,
    ) -> None:
        """缓存成功答案，并按插入顺序限制内存条目数量。"""
        self._cache.pop(key, None)
        while len(self._cache) >= self._cache_max_entries:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
        self._cache[key] = (
            self._clock() + self._cache_ttl_seconds,
            reply,
        )

    @staticmethod
    def _prepare_weather_question(
        question: str,
        *,
        queried_on: date,
        language: Language,
    ) -> tuple[str, str]:
        """为天气搜索补齐默认地点、相对日期和只答目标日约束。"""
        if _DAY_AFTER_TOMORROW_PATTERN.search(question):
            target_date = queried_on + timedelta(days=2)
        elif _TOMORROW_PATTERN.search(question):
            target_date = queried_on + timedelta(days=1)
        elif _TODAY_PATTERN.search(question):
            target_date = queried_on
        else:
            target_date = None

        # 民宿业务默认服务武汉；若客人已明确地点，保留原问题让搜索模型识别。
        location_hint = "" if _EXPLICIT_LOCATION_PATTERN.search(question) else "武汉"
        if language is Language.ZH:
            if target_date is not None:
                instruction = (
                    f"请查询{location_hint or '问题中指定地点'}在"
                    f"{target_date.isoformat()}的天气预报；"
                    "只回答这个目标日期，不要用今天或其他日期替代。"
                )
            else:
                instruction = (
                    f"请查询{location_hint or '问题中指定地点'}的天气预报；"
                    "先识别并明确回答客人要求的目标日期。"
                )
        else:
            if target_date is not None:
                instruction = (
                    "Search the weather forecast for "
                    f"{location_hint or 'the location in the question'} "
                    f"on {target_date.isoformat()}; answer only that target date, not today."
                )
            else:
                instruction = (
                    "Search the weather forecast for "
                    f"{location_hint or 'the location in the question'} "
                    "and state the target date explicitly."
                )
        prepared_question = (
            f"{instruction}\n原始问题：{question}"
            if language is Language.ZH
            else f"{instruction}\nOriginal question: {question}"
        )
        return prepared_question, instruction

    @staticmethod
    def _prepare_default_location_question(
        question: str,
        *,
        language: Language,
    ) -> tuple[str, str]:
        """为未指定地点的联网问题补充武汉默认地点。"""
        if _EXPLICIT_LOCATION_PATTERN.search(question):
            return question, ""
        if language is Language.ZH:
            instruction = "请按武汉市查询以下旅游问题。"
            return f"{instruction}\n原始问题：{question}", instruction
        instruction = "Search the following tourism question for Wuhan, China."
        return f"{instruction}\nOriginal question: {question}", instruction

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
        cache_key = self._cache_key(question, language, queried_on)
        cached_reply = self._get_cached_reply(cache_key)
        if cached_reply is not None:
            logger.info("旅游搜索完成：cache_hit=true duration_ms=0")
            return cached_reply

        search_question, location_instruction = (
            self._prepare_default_location_question(
                question,
                language=language,
            )
        )
        weather_instruction = ""
        if _WEATHER_PATTERN.search(question):
            search_question, weather_instruction = self._prepare_weather_question(
                question,
                queried_on=queried_on,
                language=language,
            )

        started_at = self._clock()
        priority_end = queried_on + timedelta(days=15)
        system = (
            "你是武汉民宿的旅游客服。必须使用 Web Search 的真实结果回答，"
            "最终正文使用温暖、简洁、可靠的民宿管家口吻，使用“您”；"
            "天气回复用“我帮您看了一下”自然开场，并根据搜索结果给一条实用提醒。"
            "不得为了亲和改动日期、温度、降雨、票价、开放时间、路线或来源。"
            "优先武汉政府、文旅局、景区、场馆和主办方来源。"
            f"当前日期：{queried_on.isoformat()}。"
            f"优先时间窗口：{queried_on.isoformat()} 至 "
            f"{priority_end.isoformat()}。"
            "近期问题优先展示该窗口内尚未结束的项目；不足时才补充更晚项目，"
            "并明确标注“半个月后”。不得把已经结束的活动当作近期推荐。"
            "每项活动日期必须注明完整年份。"
            "简单推荐要精简选优，优先选出最值得推荐的3项，正文控制在"
            "700至900字；规划问题给出半日或一日路线。"
            + (
                "距离问题必须直接回答起点和终点、约距离及可行交通方式；"
                "如果房源名称无法从可靠来源确认位置，要明确说明正在核实，"
                "不得改写成泛泛的旅行规划。"
                if _DISTANCE_PATTERN.search(question)
                else ""
            )
            + "不要在正文中输出网址或 Markdown 链接。"
            + "未指定地点的旅游联网问题默认按武汉市查询；"
            "仅客人明确指定其他地点时才使用其他地点。"
            + location_instruction
            + (
                "天气问题必须明确回答目标日期、最高/最低气温和降雨概率；"
                + weather_instruction
                if weather_instruction
                else ""
            )
            if language is Language.ZH
            else (
                "You are a warm, concise, and reliable homestay host in Wuhan. "
                "Use polite, natural language and address the guest as 'you' "
                "without salesy expressions. Use Web Search evidence, prefer "
                "official sources, and do not include URLs. Do not change dates, "
                "temperatures, prices, availability, or sources for friendliness. "
                f"Current date: {queried_on.isoformat()}. Priority window: "
                f"{queried_on.isoformat()} through {priority_end.isoformat()}. "
                "For recent requests, prioritize events that have not ended "
                "within this window, include the full year for every event "
                "date, and label later events clearly. Select the three best "
                "recommendations and keep the answer concise at 120-180 words."
                " Unspecified tourism locations default to Wuhan, China; "
                "only an explicitly named location overrides that default."
                + location_instruction
                + (
                    " For weather questions, state the target date, high/low "
                    "temperature, and precipitation probability explicitly. "
                    + weather_instruction
                    if weather_instruction
                    else ""
                )
            )
        )
        text = ""
        citations: list[tuple[str, str]] = []
        for attempt in range(2):
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    # 保留 DeepSeek 思考块与原生搜索过程；只对已有证据但遗漏正文
                    # 的间歇响应做一次有限重试。
                    max_tokens=3000,
                    system=(
                        system
                        + "完成搜索后，结束前必须输出一段客人可见的最终正文。"
                    ),
                    messages=[{"role": "user", "content": search_question}],
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
                    "unsupported"
                    if status_code in {400, 404, 422}
                    else "degraded"
                )
                self._set_status(status)
                logger.info(
                    "旅游搜索完成：cache_hit=false success=false duration_ms=%d",
                    round((self._clock() - started_at) * 1000),
                )
                raise TourismSearchError(status) from error

            text, citations = self._extract_content(response)
            if text and citations:
                break
            if citations and not text and attempt == 0:
                logger.info("旅游搜索已有证据但缺少正文，执行有限重试：attempt=2")
                continue
            break

        if not text or not citations:
            self._set_status("degraded")
            logger.info(
                "旅游搜索完成：cache_hit=false success=false duration_ms=%d",
                round((self._clock() - started_at) * 1000),
            )
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
                logger.info(
                    "旅游搜索完成：cache_hit=false success=false duration_ms=%d",
                    round((self._clock() - started_at) * 1000),
                )
                raise TourismSearchError("degraded")
        try:
            reply = format_tourism_reply(
                text,
                citations,
                queried_on,
                language=language.value,
                category=self._reply_category(question),
            )
        except ValueError as error:
            # 没有可读来源名称时不能把域名或模型常识冒充客人侧实时依据。
            self._set_status("degraded")
            logger.info(
                "旅游搜索完成：cache_hit=false success=false duration_ms=%d",
                round((self._clock() - started_at) * 1000),
            )
            raise TourismSearchError("degraded") from error
        self._store_cached_reply(cache_key, reply)
        self._set_status("ok")
        logger.info(
            "旅游搜索完成：cache_hit=false success=true duration_ms=%d",
            round((self._clock() - started_at) * 1000),
        )
        return reply
