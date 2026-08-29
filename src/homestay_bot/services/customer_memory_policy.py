import hashlib
import re
from collections.abc import Iterable

from homestay_bot.domain.enums import CustomerMemoryEvidenceType
from homestay_bot.services.guest_reply_policy import (
    contains_sensitive_guest_text,
    redact_sensitive_guest_text,
)

_MEMORY_SECRET_PATTERNS = (
    re.compile(
        r"(?:门锁|房门|大门|门禁|密码|验证码)\s*(?:密码|码)?\s*[:：]?\s*"
        r"[A-Za-z0-9]{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"(?:二维码|QR\s*code)\s*[:：]?\s*\S+", re.IGNORECASE),
)
_DYNAMIC_MEMORY_PATTERN = re.compile(
    r"(?:价格|房价|房态|空房|库存|付款|支付|退款|退订|取消|改期|"
    r"订单|预订|入住日期|退房日期|入住时间|退房时间|"
    r"\d+(?:\.\d+)?\s*元|门锁|密码|验证码|二维码|QR\s*code)",
    re.IGNORECASE,
)
_INSTRUCTION_PATTERN = re.compile(
    r"(?:忽略|无视|覆盖|绕过|泄露|输出|始终回答|必须回答|调用工具|执行命令|"
    r"system\s*(?::|prompt)|developer\s*(?::|message)|"
    r"ignore\s+(?:all\s+)?(?:previous|prior|other)\s+instructions?|"
    r"reveal\s+(?:the\s+)?(?:system\s+)?prompt|"
    r"call\s+(?:the\s+)?tool|execute\s+(?:the\s+)?command)",
    re.IGNORECASE,
)
_CORRECTION_PATTERN = re.compile(
    r"(?:不是.{0,24}(?:而是|改成|改叫|现在叫)|"
    r"(?:请)?(?:更正|纠正|更新|修改|改为|改成|改叫)|"
    r"以前.{0,20}现在|不再.{0,20}(?:了|而是)|"
    r"actually|correction|please\s+(?:change|update|correct)|"
    r"used\s+to.{0,30}now)",
    re.IGNORECASE,
)
_HISTORICAL_QUERY_PATTERN = re.compile(
    r"(?:以前|过去|之前|上次|曾经|历史|记录|变化|变更|当时|"
    r"我说过|提到过|记得.{0,12}吗|"
    r"previously|before|history|historical|used\s+to|change\s+log)",
    re.IGNORECASE,
)
_SUBJECT_KEY_PATTERN = re.compile(r"[^a-z0-9_\u4e00-\u9fff]+")
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")

_SUBJECT_ALIASES = {
    "dog_name": "pet_dog_name",
    "pet_name_dog": "pet_dog_name",
    "cat_name": "pet_cat_name",
    "pet_name_cat": "pet_cat_name",
    "preferred_floor": "floor_preference",
    "noise_preference": "quiet_preference",
    "preferred_bed": "bed_preference",
    "contact_preference": "communication_preference",
    "food_preference": "dietary_preference",
}
_CONTROLLED_SUBJECTS = frozenset(
    {
        "pet_dog_name",
        "pet_cat_name",
        "floor_preference",
        "quiet_preference",
        "bed_preference",
        "communication_preference",
        "dietary_preference",
    }
)
_SUBJECT_QUERY_TERMS = {
    "pet_dog_name": ("狗", "小狗", "宠物", "dog", "puppy", "名字", "叫什么"),
    "pet_cat_name": ("猫", "小猫", "宠物", "cat", "kitten", "名字", "叫什么"),
    "floor_preference": ("楼层", "高楼层", "低楼层", "floor"),
    "quiet_preference": ("安静", "噪音", "吵", "quiet", "noise"),
    "bed_preference": ("床", "大床", "双床", "bed", "twin"),
    "communication_preference": ("联系", "沟通", "微信", "电话", "contact", "message"),
    "dietary_preference": ("饮食", "忌口", "过敏", "素食", "diet", "allergy"),
}
_EVIDENCE_RANKS = {
    CustomerMemoryEvidenceType.MODEL_INFERENCE.value: 0,
    CustomerMemoryEvidenceType.USER_EXPLICIT.value: 1,
    CustomerMemoryEvidenceType.EMPLOYEE_CONFIRMED.value: 2,
    "admin_confirmed": 3,
}


def redact_memory_text(text: str) -> str:
    """用统一占位符脱敏联系信息、身份信息、地址和入住凭证。"""
    redacted = redact_sensitive_guest_text(text)
    for pattern in _MEMORY_SECRET_PATTERNS:
        redacted = pattern.sub("[敏感信息已隐藏]", redacted)
    return redacted


def contains_sensitive_memory_text(text: str) -> bool:
    """判断文本是否仍包含不应进入摘要或长期记忆的敏感字段。"""
    return contains_sensitive_guest_text(text) or any(
        pattern.search(text) for pattern in _MEMORY_SECRET_PATTERNS
    )


def normalize_source_text(text: str) -> str:
    """脱敏并压缩空白，得到引用验证和指纹计算的统一文本。"""
    return " ".join(redact_memory_text(text).split()).strip().casefold()


def verify_source_excerpt(excerpt: str | None, source: str) -> bool:
    """验证脱敏引用确实连续出现在来源原文中，拒绝空引用和纯占位符。"""
    normalized_excerpt = normalize_source_text(excerpt or "")
    normalized_source = normalize_source_text(source)
    if not normalized_excerpt or not normalized_source:
        return False
    meaningful = normalized_excerpt.replace("[敏感信息已隐藏]", "").strip(" ，。！？,.;:：")
    if len(meaningful) < 2:
        return False
    return normalized_excerpt in normalized_source


def source_excerpt_hash(excerpt: str) -> str:
    """计算脱敏规范化引用的稳定 SHA-256 指纹。"""
    return hashlib.sha256(normalize_source_text(excerpt).encode("utf-8")).hexdigest()


def evidence_rank(evidence: CustomerMemoryEvidenceType | str) -> int:
    """返回证据强度，未知类型按最低可信处理。"""
    value = evidence.value if isinstance(evidence, CustomerMemoryEvidenceType) else evidence
    return _EVIDENCE_RANKS.get(value, -1)


def stronger_evidence(
    current: CustomerMemoryEvidenceType, incoming: CustomerMemoryEvidenceType
) -> CustomerMemoryEvidenceType:
    """选择更强证据，证据相同时保留现有来源以避免无意义覆盖。"""
    if evidence_rank(incoming) > evidence_rank(current):
        return incoming
    return current


def normalize_subject_key(subject_key: str) -> str:
    """规范主题键并把常见模型别名收敛到受控主题。"""
    normalized = _SUBJECT_KEY_PATTERN.sub("_", subject_key.casefold()).strip("_")[:128]
    normalized = normalized or "general"
    return _SUBJECT_ALIASES.get(normalized, normalized)


def can_auto_activate_subject(subject_key: str) -> bool:
    """判断主题是否允许通过本地确定性证据自动晋级。"""
    return normalize_subject_key(subject_key) in _CONTROLLED_SUBJECTS


def is_dynamic_memory_text(text: str) -> bool:
    """判断文本是否包含必须实时查询、不可长期固化的业务状态。"""
    return bool(_DYNAMIC_MEMORY_PATTERN.search(text))


def is_instruction_like_memory(text: str) -> bool:
    """识别提示覆盖、工具调用等不应被当作客户事实的指令内容。"""
    return bool(_INSTRUCTION_PATTERN.search(text))


def is_explicit_correction(text: str) -> bool:
    """仅在原文含明确纠正或变化措辞时允许自动纠正。"""
    return bool(_CORRECTION_PATTERN.search(text))


def is_historical_query(text: str) -> bool:
    """判断当前问题是否明确需要事实变化或历史事件。"""
    return bool(_HISTORICAL_QUERY_PATTERN.search(text))


def candidate_value_is_grounded(
    statement: str, excerpt: str, *, subject_key: str = "general"
) -> bool:
    """按受控主题提取确定性值，禁止仅凭共享主题词证明候选。"""
    normalized_subject = normalize_subject_key(subject_key)
    statement_value = _extract_controlled_value(normalized_subject, statement)
    excerpt_value = _extract_controlled_value(normalized_subject, excerpt)
    return statement_value is not None and statement_value == excerpt_value


def is_safe_memory_candidate(
    *, subject_key: str, statement: str, source_excerpt: str | None, source: str
) -> bool:
    """组合执行候选自动晋级前可本地确定的安全检查。"""
    return (
        can_auto_activate_subject(subject_key)
        and verify_source_excerpt(source_excerpt, source)
        and candidate_value_is_grounded(
            statement,
            source_excerpt or "",
            subject_key=subject_key,
        )
        and not contains_sensitive_memory_text(statement)
        and not is_dynamic_memory_text(f"{subject_key} {statement}")
        and not is_instruction_like_memory(f"{statement} {source_excerpt or ''}")
    )


def memory_relevance_score(
    query: str, *, subject_key: str, statement: str
) -> float:
    """使用主题别名和轻量词元计算本地召回分数，不触发额外模型调用。"""
    normalized_subject = normalize_subject_key(subject_key)
    query_folded = query.casefold()
    score = 0.0
    for term in _SUBJECT_QUERY_TERMS.get(normalized_subject, ()):
        if term.casefold() in query_folded:
            score += 2.0
    query_tokens = _meaningful_tokens(query)
    memory_tokens = _meaningful_tokens(f"{normalized_subject} {statement}")
    score += float(len(query_tokens & memory_tokens))
    return score


def _meaningful_tokens(text: str) -> set[str]:
    """提取中英文词元并排除长期记忆中高频但无区分度的称谓。"""
    ignored = {"客", "户", "的", "我", "是", "叫", "喜", "欢", "customer", "guest"}
    return {
        token
        for token in _tokenize_with_bigrams(redact_memory_text(text).casefold())
        if token not in ignored
    }


def _extract_controlled_value(subject_key: str, text: str) -> str | None:
    """从允许自动晋级的主题中提取可比较值；无法确定时保守拒绝。"""
    normalized = normalize_source_text(text)
    if subject_key in {"pet_dog_name", "pet_cat_name"}:
        animal = "狗" if subject_key == "pet_dog_name" else "猫"
        match = re.search(
            rf"{animal}(?:狗|咪)?(?:的)?(?:名字)?(?:叫|是|为)\s*"
            r"([\u4e00-\u9fffA-Za-z0-9_-]{1,20})",
            normalized,
        )
        return f"name:{match.group(1).casefold()}" if match else None
    if subject_key == "floor_preference":
        return _first_matching_value(
            normalized,
            (
                ("high", r"高楼层|高层|楼层高"),
                ("low", r"低楼层|低层|楼层低"),
                ("middle", r"中楼层|中层"),
                ("ground", r"一楼|首层|底楼"),
            ),
        )
    if subject_key == "quiet_preference":
        return _first_matching_value(
            normalized,
            (
                ("not_quiet", r"不(?:喜欢|要|需要|偏好).{0,4}安静"),
                ("quiet", r"(?:喜欢|要|需要|偏好).{0,5}安静|不.{0,4}(?:吵|噪音)"),
            ),
        )
    if subject_key == "bed_preference":
        return _first_matching_value(
            normalized,
            (
                ("twin", r"双床|两张床|twin\s*bed"),
                ("queen", r"大床|双人床|queen\s*bed|king\s*bed"),
                ("single", r"单人床|single\s*bed"),
            ),
        )
    if subject_key == "communication_preference":
        return _first_matching_value(
            normalized,
            (
                ("wechat", r"微信|we\s*chat"),
                ("phone", r"电话|打给|call"),
                ("text", r"短信|文字(?:联系|沟通)|text\s*message"),
            ),
        )
    if subject_key == "dietary_preference":
        if re.search(r"素食|吃素|vegetarian|vegan", normalized):
            return "diet:vegetarian"
        avoid_match = re.search(
            r"(?:不吃|不能吃|忌口|对)\s*([\u4e00-\u9fff]{1,12}?)"
            r"(?=\s*(?:过敏|[，。！？,.;]|$))",
            normalized,
        )
        if avoid_match:
            return f"avoid:{avoid_match.group(1)}"
        allergy_match = re.search(r"([\u4e00-\u9fff]{1,12})\s*过敏", normalized)
        if allergy_match:
            return f"avoid:{allergy_match.group(1)}"
    return None


def _first_matching_value(
    text: str, candidates: tuple[tuple[str, str], ...]
) -> str | None:
    """按优先级返回第一个确定性分类值。"""
    for value, pattern in candidates:
        if re.search(pattern, text, re.IGNORECASE):
            return value
    return None


def _tokenize_with_bigrams(text: str) -> Iterable[str]:
    """保留英文词和中文单字，同时补充中文二元组改善短问句召回。"""
    tokens = _TOKEN_PATTERN.findall(text)
    yield from tokens
    chinese = "".join(token for token in tokens if "\u4e00" <= token <= "\u9fff")
    for index in range(len(chinese) - 1):
        yield chinese[index : index + 2]
