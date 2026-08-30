"""把企业微信安全拦截的客人回复改写为事实等价的纯文本。"""

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.tourism import split_tourism_reply
from homestay_bot.services.guest_reply_policy import (
    contains_sensitive_guest_text,
    redact_sensitive_guest_text,
    remove_ungrounded_property_claims,
    sanitize_guest_reply,
)
from homestay_bot.services.model_budget import MODEL_BUDGET, serialized_chars

_URL_OR_MARKDOWN_PATTERN = re.compile(
    r"https?://|www\.|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b|"
    r"\[[^\]]+\]\([^)]+\)|\*{1,3}\S|_{1,3}\S",
    re.IGNORECASE,
)
_SENSITIVE_OUTPUT_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"二维码|微信号|联系(?:电话|方式)|(?:详细|具体)?地址\s*[:：]|address\s*:",
    re.IGNORECASE,
)
_TECHNICAL_LABEL_PATTERN = re.compile(
    r"查询日期\s*[:：]|参考来源\s*[:：]|Query date\s*:|Sources?\s*:",
    re.IGNORECASE,
)
_NUMBER_FACT_PATTERN = re.compile(
    r"\d+(?:[./:～~\-]\d+)*(?:\s*(?:℃|°C|%|公里|千米|km|元))?",
    re.IGNORECASE,
)
_CHINESE_NUMBER_FACT_PATTERN = re.compile(
    r"[零〇一二两三四五六七八九十百千]+(?:天|晚|小时|分钟|公里|千米|元|点|人|张)"
)
_DATE_FACT_PATTERN = re.compile(
    r"(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|"
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
)
_WEATHER_FACT_PATTERN = re.compile(
    r"雷阵雨|阵雨|雷雨|小雨|中雨|大雨|暴雨|雨夹雪|小雪|中雪|大雪|"
    r"晴|多云|阴|thunderstorms?|showers?|light rain|moderate rain|"
    r"heavy rain|sunny|cloudy|overcast|snow",
    re.IGNORECASE,
)
_LOCATION_SUFFIX_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,12}(?:市|区|县|镇|街道|机场|火车站|车站|景区|"
    r"公园|博物馆|大学|医院|酒店|民宿|广场|大厦|塔|楼|湖|山|江|河|站)"
)
_WEATHER_LOCATION_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,10})(?=(?:\d{1,2}月\d{1,2}日|今天|明天|后天).{0,5}"
    r"(?:有|天气|气温|温度))"
)
_MUNICIPALITY_PATTERN = re.compile(r"北京|上海|天津|重庆|武汉")
_KNOWN_EN_CITY_PATTERN = re.compile(
    r"\b(?:Wuhan|Beijing|Shanghai|Tianjin|Chongqing)\b",
    re.IGNORECASE,
)
_EN_LOCATION_CONTEXT_PATTERN = re.compile(
    r"\b(?:in|to|from|near|at)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\b"
)
_EN_PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b")
_EN_NON_LOCATION_TITLES = {
    "a",
    "an",
    "august",
    "december",
    "february",
    "friday",
    "january",
    "july",
    "june",
    "march",
    "may",
    "monday",
    "november",
    "october",
    "please",
    "saturday",
    "september",
    "showers",
    "sunday",
    "temperatures",
    "the",
    "thursday",
    "tickets",
    "tuesday",
    "wednesday",
    "weather",
}
_NEGATION_PATTERN = re.compile(
    r"(?:不必|不用|不能|不可|不得|无需|未曾|没有|尚未)|"
    r"\b(?:not|no|never|cannot|can't|won't|doesn't|don't|isn't|hasn't|haven't)\b",
    re.IGNORECASE,
)
_EN_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SAFE_NEW_EN_WORDS = {
    "a",
    "an",
    "are",
    "around",
    "approximately",
    "as",
    "be",
    "expected",
    "for",
    "in",
    "information",
    "is",
    "kindly",
    "of",
    "on",
    "please",
    "remember",
    "summary",
    "the",
    "to",
}
_SAFE_NEW_ZH_CHARS = set("为的是在于预计可能大约左右请您记得注意温馨提醒简报信息")
_CLAIM_CONNECTOR_TEXT = (
    r"\b(?:but|and|or|nor|plus|also|whereas|while|although|though|yet)\b|"
    r"\b(?:as\s+well\s+as|along\s+with)\b|"
    r"但是|不过|然而|并且|同时|以及|但|而|却|且|与|和|或|也|还|又"
)
_CLAUSE_SPLIT_PATTERN = re.compile(
    rf"[，,；;。.!?！？]+|{_CLAIM_CONNECTOR_TEXT}",
    re.IGNORECASE,
)
_CLAIM_CONNECTOR_PATTERN = re.compile(_CLAIM_CONNECTOR_TEXT, re.IGNORECASE)
_FACT_TOPIC_PATTERN = re.compile(
    r"地铁|公交|早餐|停车|metro|subway|bus|breakfast|parking",
    re.IGNORECASE,
)
_EN_CLAUSE_SUBJECT_PATTERN = re.compile(
    r"^(?:the\s+)?([a-z][a-z'-]*(?:\s+[a-z][a-z'-]*){0,5}?)"
    r"(?=\s+(?:is|are|was|were|has|have|will|does|do|can|cannot|can't|"
    r"remains?|costs?|comes?)\b)",
    re.IGNORECASE,
)
_ZH_CLAUSE_SUBJECT_PATTERN = re.compile(
    r"^([\u4e00-\u9fff]{1,12}?)(?=(?:不|没|未|无|能|可|会|将|已|"
    r"开放|关闭|暂停|正常|支持|需要|包含|到达|使用))"
)
_RELATION_STOP_EN_WORDS = {
    "a",
    "an",
    "are",
    "be",
    "been",
    "do",
    "does",
    "has",
    "have",
    "is",
    "the",
    "will",
}
_RELATION_STOP_ZH_CHARS = set("的是在于")
_NON_FACT_CLAUSE_PATTERN = re.compile(
    r"记得|请您|提醒|注意|小心|建议|remember|please|take care|be careful",
    re.IGNORECASE,
)
_NEW_ADVICE_PATTERN = re.compile(
    r"推荐|建议|不妨|可以去|可前往|值得去|consider|recommend|suggest",
    re.IGNORECASE,
)
_SEMANTIC_FACT_PATTERNS = (
    re.compile(
        r"(?:气温|温度|temperature)[^\d]{0,8}"
        r"(\d+(?:[～~\-]\d+)?\s*(?:℃|°C))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:降雨概率|概率|chance of rain)[^\d]{0,8}(\d+%)", re.IGNORECASE),
    re.compile(
        r"(?:门票|票价|price|admission)[^\d]{0,12}(\d+(?:\.\d+)?元?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:距离|约|distance)[^\d]{0,8}(\d+(?:\.\d+)?\s*(?:公里|千米|km))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:需要|耗时|约|takes?)[^\d]{0,8}"
        r"(\d+\s*(?:小时|分钟|hours?|minutes?))",
        re.IGNORECASE,
    ),
)
_KEY_FACT_PATTERNS = (
    re.compile(r"武汉|Wuhan", re.IGNORECASE),
    re.compile(r"阵雨|降雨|下雨|雷雨|rain|shower|storm", re.IGNORECASE),
    re.compile(r"晴|多云|阴|sunny|cloudy|overcast", re.IGNORECASE),
    re.compile(r"门票|票价|ticket|admission", re.IGNORECASE),
    re.compile(r"开放|闭馆|营业|open|clos", re.IGNORECASE),
    re.compile(r"地铁|公交|步行|打车|metro|subway|bus|walk|taxi", re.IGNORECASE),
)
_CRITICAL_CLAIM_PATTERNS = (
    re.compile(r"免费|免票|free admission", re.IGNORECASE),
    re.compile(r"停运|停业|关闭|取消|closed|cancelled", re.IGNORECASE),
    re.compile(r"售罄|满房|sold out|fully booked", re.IGNORECASE),
)
_ATOMIC_FACT_PATTERNS = (
    re.compile(
        r"无需预约|免预约|需要预约|需预约|"
        r"no reservation required|reservation required",
        re.IGNORECASE,
    ),
    re.compile(
        r"正常开放|照常开放|暂停开放|临时关闭|"
        r"open as usual|temporarily closed",
        re.IGNORECASE,
    ),
    re.compile(
        r"步行可达|地铁直达|公交直达|打车可达|"
        r"within walking distance|walkable|direct (?:metro|subway|bus)",
        re.IGNORECASE,
    ),
    re.compile(
        r"照常举行|正常举行|延期举行|活动延期|"
        r"as scheduled|postponed|event cancelled",
        re.IGNORECASE,
    ),
    re.compile(r"支持刷卡|可以刷卡|只收现金|card accepted|cash only", re.IGNORECASE),
)
_EN_POLARITY_FACT_PATTERNS = (
    (
        "event_cancelled",
        re.compile(r"\b(?:event\s+)?(?:is\s+)?(not\s+)?cancelled\b", re.IGNORECASE),
    ),
    (
        "reservation_required",
        re.compile(
            r"\b(?:(no)\s+)?reservation\s+(?:is\s+)?(not\s+)?required\b",
            re.IGNORECASE,
        ),
    ),
    (
        "open",
        re.compile(r"\b(?:is|will be|remains?)\s+(not\s+)?open\b", re.IGNORECASE),
    ),
    (
        "walkable",
        re.compile(r"\b(?:is|are)\s+(not\s+)?walkable\b", re.IGNORECASE),
    ),
)


class DeliveryRewriteUnavailableError(RuntimeError):
    """表示模型改写无法通过本地事实与安全校验。"""


class _DeliveryRewritePayload(BaseModel):
    """约束模型只返回一段客人可见正文。"""

    reply_text: str = Field(min_length=1, max_length=1500)


class DeepSeekDeliveryRewriter:
    """使用现有 DeepSeek 客户端执行一次无工具安全改写。"""

    def __init__(self, *, client: Any, model: str) -> None:
        """保存共享模型客户端和运行时模型名称。"""
        self._client = client
        self._model = model

    @staticmethod
    def _minimize_text(content: str) -> str:
        """从模型输入中移除常见联系方式和网址。"""
        minimized = redact_sensitive_guest_text(content)
        minimized = re.sub(
            r"(?:我叫|姓名(?:是|[:：])?)\s*[\u4e00-\u9fff]{2,4}",
            "[姓名已隐藏]",
            minimized,
        )
        minimized = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[邮箱已隐藏]",
            minimized,
            flags=re.IGNORECASE,
        )
        return re.sub(r"https?://\S+|www\.\S+", "[链接已移除]", minimized)

    @staticmethod
    def _validate_facts(original: str, rewritten: str) -> None:
        """拒绝新增或丢失数字事实，并保留已出现的核心事实类别。"""
        original_numbers = _NUMBER_FACT_PATTERN.findall(original)
        rewritten_numbers = _NUMBER_FACT_PATTERN.findall(rewritten)
        original_numbers.extend(_CHINESE_NUMBER_FACT_PATTERN.findall(original))
        rewritten_numbers.extend(_CHINESE_NUMBER_FACT_PATTERN.findall(rewritten))
        if Counter(original_numbers) != Counter(rewritten_numbers):
            raise DeliveryRewriteUnavailableError("改写数字事实发生变化")
        if Counter(_DATE_FACT_PATTERN.findall(original)) != Counter(
            _DATE_FACT_PATTERN.findall(rewritten)
        ):
            raise DeliveryRewriteUnavailableError("改写日期事实发生变化")
        if Counter(
            item.lower() for item in _WEATHER_FACT_PATTERN.findall(original)
        ) != Counter(
            item.lower() for item in _WEATHER_FACT_PATTERN.findall(rewritten)
        ):
            raise DeliveryRewriteUnavailableError("改写天气事实发生变化")
        if DeepSeekDeliveryRewriter._locations(original) != DeepSeekDeliveryRewriter._locations(
            rewritten
        ):
            raise DeliveryRewriteUnavailableError("改写地点事实发生变化")
        if _NEW_ADVICE_PATTERN.search(rewritten) and not _NEW_ADVICE_PATTERN.search(original):
            raise DeliveryRewriteUnavailableError("改写新增建议或推荐")
        for pattern in _SEMANTIC_FACT_PATTERNS:
            if Counter(item.lower() for item in pattern.findall(original)) != Counter(
                item.lower() for item in pattern.findall(rewritten)
            ):
                raise DeliveryRewriteUnavailableError("改写改变数字事实含义")
        for pattern in _KEY_FACT_PATTERNS:
            if pattern.search(original) and not pattern.search(rewritten):
                raise DeliveryRewriteUnavailableError("改写丢失关键事实")
        for pattern in _CRITICAL_CLAIM_PATTERNS:
            if bool(pattern.search(original)) != bool(pattern.search(rewritten)):
                raise DeliveryRewriteUnavailableError("改写改变关键结论")
        for pattern in _ATOMIC_FACT_PATTERNS:
            if Counter(item.lower() for item in pattern.findall(original)) != Counter(
                item.lower() for item in pattern.findall(rewritten)
            ):
                raise DeliveryRewriteUnavailableError("改写改变无数字核心事实")
        if DeepSeekDeliveryRewriter._english_polarity_facts(
            original
        ) != DeepSeekDeliveryRewriter._english_polarity_facts(rewritten):
            raise DeliveryRewriteUnavailableError("改写改变英文事实极性")
        if Counter(item.lower() for item in _NEGATION_PATTERN.findall(original)) != Counter(
            item.lower() for item in _NEGATION_PATTERN.findall(rewritten)
        ):
            raise DeliveryRewriteUnavailableError("改写改变否定事实")
        original_bindings = DeepSeekDeliveryRewriter._claim_bindings(original)
        rewritten_bindings = DeepSeekDeliveryRewriter._claim_bindings(rewritten)
        if original_bindings is None or rewritten_bindings is None:
            raise DeliveryRewriteUnavailableError("改写含无法验证的多事实关系")
        original_anchor_count = len({binding[0] for binding in original_bindings})
        rewritten_anchor_count = len({binding[0] for binding in rewritten_bindings})
        original_anchor_set = {binding[0] for binding in original_bindings}
        rewritten_anchor_set = {binding[0] for binding in rewritten_bindings}
        if original_anchor_set != rewritten_anchor_set:
            raise DeliveryRewriteUnavailableError("改写改变事实实体")
        has_multiple_claims = (
            max(sum(original_bindings.values()), sum(rewritten_bindings.values())) >= 2
        )
        requires_relation_binding = (
            max(original_anchor_count, rewritten_anchor_count) >= 2
            or bool(_CLAIM_CONNECTOR_PATTERN.search(original))
            or bool(_CLAIM_CONNECTOR_PATTERN.search(rewritten))
            or has_multiple_claims
        )
        if requires_relation_binding and original_bindings != rewritten_bindings:
            raise DeliveryRewriteUnavailableError("改写把事实绑定到错误实体")
        DeepSeekDeliveryRewriter._validate_content_vocabulary(original, rewritten)

    @staticmethod
    def _english_polarity_facts(content: str) -> Counter[tuple[str, bool]]:
        """提取英文事实及其肯定/否定极性，防止改写删除 not 或 no。"""
        facts: Counter[tuple[str, bool]] = Counter()
        for fact_name, pattern in _EN_POLARITY_FACT_PATTERNS:
            for match in pattern.finditer(content):
                is_positive = not any(match.groups())
                facts[(fact_name, is_positive)] += 1
        return facts

    @staticmethod
    def _validate_content_vocabulary(original: str, rewritten: str) -> None:
        """只允许新增连接与礼貌措辞，无法证明来源的实词一律降级。"""
        original_zh = set(re.findall(r"[\u4e00-\u9fff]", original))
        rewritten_zh = set(re.findall(r"[\u4e00-\u9fff]", rewritten))
        if rewritten_zh - original_zh - _SAFE_NEW_ZH_CHARS:
            raise DeliveryRewriteUnavailableError("改写新增中文事实词")

        original_en = {item.lower() for item in _EN_WORD_PATTERN.findall(original)}
        rewritten_en = {item.lower() for item in _EN_WORD_PATTERN.findall(rewritten)}
        if rewritten_en - original_en - _SAFE_NEW_EN_WORDS:
            raise DeliveryRewriteUnavailableError("改写新增英文事实词")

    @staticmethod
    def _claim_bindings(
        content: str,
    ) -> (
        Counter[
            tuple[
                str,
                tuple[str, ...],
                tuple[str, ...],
                tuple[str, ...],
                tuple[str, ...],
            ]
        ]
        | None
    ):
        """把多实体正文转换为实体、谓词、数字和天气事实元组。"""
        bindings: Counter[
            tuple[
                str,
                tuple[str, ...],
                tuple[str, ...],
                tuple[str, ...],
                tuple[str, ...],
            ]
        ] = Counter()
        clauses = [
            item.strip()
            for item in _CLAUSE_SPLIT_PATTERN.split(content)
            if item.strip() and not _NON_FACT_CLAUSE_PATTERN.search(item)
        ]
        clause_anchors: list[set[str]] = []
        for clause in clauses:
            anchors = DeepSeekDeliveryRewriter._clause_anchors(clause)
            clause_anchors.append(anchors)
        all_anchors = set().union(*clause_anchors) if clause_anchors else set()
        if len(clauses) >= 2 and not all_anchors:
            return None
        if len(all_anchors) == 1:
            sole_anchor = next(iter(all_anchors))
            clause_anchors = [anchors or {sole_anchor} for anchors in clause_anchors]
        elif len(clauses) >= 2 and any(not anchors for anchors in clause_anchors):
            return None

        for clause, anchors in zip(clauses, clause_anchors, strict=True):
            if not anchors:
                continue
            is_negative = bool(_NEGATION_PATTERN.search(clause))
            predicates: list[str] = []
            if re.search(r"关闭|闭馆|closed", clause, re.IGNORECASE):
                predicates.append(f"open:{is_negative}")
            elif re.search(r"开放|营业|\bopen\b", clause, re.IGNORECASE):
                predicates.append(f"open:{not is_negative}")
            if re.search(r"直达|\bdirect\b", clause, re.IGNORECASE):
                predicates.append(f"direct:{not is_negative}")
            if re.search(r"步行|walkable|\bwalk\b", clause, re.IGNORECASE):
                predicates.append(f"walkable:{not is_negative}")
            if re.search(r"包含|included", clause, re.IGNORECASE):
                predicates.append(f"included:{not is_negative}")
            if re.search(r"可用|available", clause, re.IGNORECASE):
                predicates.append(f"available:{not is_negative}")
            if re.search(r"预约|reservation", clause, re.IGNORECASE):
                predicates.append(f"reservation:{not is_negative}")
            if re.search(r"取消|cancelled", clause, re.IGNORECASE):
                predicates.append(f"cancelled:{not is_negative}")

            numbers = tuple(sorted(_NUMBER_FACT_PATTERN.findall(clause)))
            weather = tuple(
                sorted(item.lower() for item in _WEATHER_FACT_PATTERN.findall(clause))
            )
            signature = (
                tuple(sorted(predicates)),
                numbers,
                weather,
            )
            for anchor in anchors:
                remaining = re.sub(re.escape(anchor), " ", clause, flags=re.IGNORECASE)
                relation_words = {
                    item.lower()
                    for item in _EN_WORD_PATTERN.findall(remaining)
                    if item.lower() not in _RELATION_STOP_EN_WORDS
                }
                relation_words.update(
                    item
                    for item in re.findall(r"[\u4e00-\u9fff]", remaining)
                    if item not in _RELATION_STOP_ZH_CHARS
                )
                bindings[(anchor, *signature, tuple(sorted(relation_words)))] += 1
        return bindings

    @staticmethod
    def _clause_anchors(clause: str) -> set[str]:
        """从单个事实分句提取地点、交通主题或通用中英文主语。"""
        anchors = {
            *DeepSeekDeliveryRewriter._locations(clause),
            *(item.lower() for item in _FACT_TOPIC_PATTERN.findall(clause)),
        }
        english_subject = _EN_CLAUSE_SUBJECT_PATTERN.search(clause)
        chinese_subject = _ZH_CLAUSE_SUBJECT_PATTERN.search(clause)
        if english_subject and not anchors:
            anchors.add(english_subject.group(1).lower())
        if chinese_subject and not anchors:
            anchors.add(chinese_subject.group(1))
        return anchors

    @staticmethod
    def _locations(content: str) -> list[str]:
        """保守提取中英文地点实体，宁可降级也不放行地点篡改。"""
        candidates = [
            *_WEATHER_LOCATION_PATTERN.findall(content),
            *_MUNICIPALITY_PATTERN.findall(content),
            *_LOCATION_SUFFIX_PATTERN.findall(content),
            *_KNOWN_EN_CITY_PATTERN.findall(content),
            *_EN_LOCATION_CONTEXT_PATTERN.findall(content),
            *(
                item
                for item in _EN_PROPER_NOUN_PATTERN.findall(content)
                if item.lower() not in _EN_NON_LOCATION_TITLES
            ),
        ]
        normalized: list[str] = []
        for item in candidates:
            value = re.sub(r"^(?:推荐去|建议去|前往|位于|从|到)", "", item).strip()
            normalized_value = value.lower()
            if normalized_value and normalized_value not in normalized:
                normalized.append(normalized_value)
        return normalized

    async def rewrite(
        self,
        *,
        guest_question: str,
        blocked_reply: str,
        language: Language,
    ) -> str:
        """执行一次改写，并只返回通过确定性校验的客人正文。"""
        original_body, _evidence_footer = split_tourism_reply(blocked_reply)
        if not original_body.strip():
            raise DeliveryRewriteUnavailableError("原回复缺少可改写正文")
        try:
            rewrite_request = {
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是民宿客服安全改写编辑。请把被平台拒绝的回复重新组织为"
                            "自然、简洁、温暖的纯文本。必须保留原有日期、地点、数字、"
                            "天气、票价、开放时间、路线和安全提醒；不得新增或猜测事实，"
                            "不得写民宿设施、服务承诺、网址、来源、查询过程或内部标签。"
                            "不要调用工具或联网。只输出 JSON："
                            '{"reply_text":"改写后的完整回复"}。'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"语言：{language.value}\n"
                            f"客人问题：{self._minimize_text(guest_question)}\n"
                            f"原回复：{self._minimize_text(original_body)}"
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": MODEL_BUDGET.delivery_rewrite_max_tokens,
                "extra_body": {"thinking": {"type": "disabled"}},
            }
            if serialized_chars(rewrite_request) > MODEL_BUDGET.main_request_chars:
                raise DeliveryRewriteUnavailableError("改写请求超过字符预算")
            response = await self._client.chat.completions.create(
                **rewrite_request
            )
            content = response.choices[0].message.content or ""
            rewritten = _DeliveryRewritePayload.model_validate_json(
                content
            ).reply_text.strip()
        except (ValidationError, AttributeError, IndexError, TypeError) as error:
            raise DeliveryRewriteUnavailableError("改写响应无效") from error
        except DeliveryRewriteUnavailableError:
            raise
        except Exception as error:
            raise DeliveryRewriteUnavailableError("改写调用失败") from error

        if _URL_OR_MARKDOWN_PATTERN.search(rewritten):
            raise DeliveryRewriteUnavailableError("改写含链接或格式标记")
        if _TECHNICAL_LABEL_PATTERN.search(rewritten):
            raise DeliveryRewriteUnavailableError("改写含内部标签")
        if _SENSITIVE_OUTPUT_PATTERN.search(rewritten):
            raise DeliveryRewriteUnavailableError("改写含联系方式或地址")
        if contains_sensitive_guest_text(rewritten):
            raise DeliveryRewriteUnavailableError("改写含敏感身份或订单信息")
        rewritten = remove_ungrounded_property_claims(rewritten)
        if not rewritten:
            raise DeliveryRewriteUnavailableError("改写只剩未经审核的民宿自述")
        rewritten = sanitize_guest_reply(
            rewritten,
            language=language,
            requires_human=False,
        ).strip()
        if not rewritten or len(rewritten) > 1500:
            raise DeliveryRewriteUnavailableError("改写正文不可发送")
        normalized_original = re.sub(r"\s+", "", original_body)
        normalized_rewritten = re.sub(r"\s+", "", rewritten)
        if normalized_original == normalized_rewritten:
            raise DeliveryRewriteUnavailableError("改写与被拦截正文相同")
        self._validate_facts(original_body, rewritten)
        return rewritten
