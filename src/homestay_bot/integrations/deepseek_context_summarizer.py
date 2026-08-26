import json
import re
from typing import Any

from pydantic import BaseModel, Field

from homestay_bot.domain.enums import (
    CustomerMemoryCategory,
    CustomerMemoryEvidenceType,
)
from homestay_bot.services.context_retention import (
    ContextSummaryResult,
    CustomerMemoryCandidate,
    MemorySource,
)


class ContextSummarySafetyError(RuntimeError):
    """表示摘要输出仍包含不可发送给模型上下文的敏感字段。"""


class _MemoryCandidatePayload(BaseModel):
    """校验模型返回的单条结构化记忆候选。"""

    subject_key: str = Field(min_length=1, max_length=128)
    category: CustomerMemoryCategory
    statement: str = Field(min_length=1, max_length=500)
    evidence_type: CustomerMemoryEvidenceType
    source_message_id: str | None = Field(default=None, max_length=128)
    confidence: float = Field(ge=0, le=1)
    is_correction: bool = False


class _SummaryPayload(BaseModel):
    """校验 DeepSeek 分层摘要的固定 JSON 结构。"""

    summary: str = Field(min_length=1, max_length=2000)
    unresolved_items: list[str] = Field(default_factory=list, max_length=20)
    memory_candidates: list[_MemoryCandidatePayload] = Field(
        default_factory=list, max_length=20
    )


class DeepSeekContextSummarizer:
    """先本地脱敏，再使用 DeepSeek 合并客户分层摘要。"""

    # 限制单条和总消息输入，避免高频会话耗尽模型上下文或请求预算。
    _MAX_MESSAGE_CHARS = 2_000
    _MAX_MESSAGES_CHARS = 12_000
    _MAX_EXISTING_SUMMARY_CHARS = 4_000

    _SENSITIVE_PATTERNS = (
        re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)"),
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)"),
        re.compile(
            r"(?:地址\s*)?(?:湖北省)?武汉市?"
            r"(?:洪山区|武昌区|青山区|汉阳区|江汉区|江岸区|硚口区|"
            r"蔡甸区|东西湖区|黄陂区|新洲区|江夏区)"
            r"[^，。\n]{0,40}(?:路|街|大道|小区|号|栋|室)[^，。\n]{0,20}"
        ),
        re.compile(r"(?:门锁|房门|密码|验证码)\s*(?:密码|码)?\s*[:：]?\s*[A-Za-z0-9]{4,}"),
        re.compile(r"(?:二维码|QR\s*code)\s*[:：]?\s*\S+", re.IGNORECASE),
    )
    _DYNAMIC_MEMORY_PATTERN = re.compile(
        r"(?:价格|房价|房态|空房|库存|付款|支付|退款|退订|取消|改期|"
        r"订单|预订|入住日期|退房日期|入住时间|退房时间|"
        r"\d+(?:\.\d+)?\s*元|门锁|密码|验证码|二维码|QR\s*code)",
        re.IGNORECASE,
    )

    def __init__(self, client: Any, model: str) -> None:
        """注入 OpenAI 兼容客户端和 DeepSeek 模型名称。"""
        self._client = client
        self._model = model

    async def summarize(
        self,
        *,
        tier: str,
        existing_summary: str,
        messages: list[MemorySource],
    ) -> ContextSummaryResult:
        """合并脱敏消息，并拒绝任何重新出现敏感特征的输出。"""
        safe_existing = self._redact(existing_summary)[
            : self._MAX_EXISTING_SUMMARY_CHARS
        ]
        safe_messages = self._bound_messages(messages)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你负责整理民宿客户上下文。只保留偏好、已确认事实和待确认项；"
                        "禁止输出手机号、身份证、详细地址、门锁密码、验证码或二维码。"
                        "价格、房态、付款退款、当前订单状态和临时承诺不得成为记忆。"
                        "memory_candidates 仅提取可跨会话复用的稳定客户事实；"
                        "summary 只能合并 summary_eligible=true 的消息；该字段为 false"
                        "的最近原文只用于提取 memory_candidates，不得写入 summary。"
                        "如果没有可进入 summary 的消息，保持 existing_summary；它为空时"
                        "令 summary 为“无新增摘要”。"
                        "subject_key 使用稳定简短的 snake_case 主题；source_message_id 必须"
                        "引用输入消息编号；无法证明为客户明示或员工确认时 evidence_type"
                        "必须为 model_inference。客户明确纠正同一主题时 is_correction=true。"
                        "只输出 JSON：summary、unresolved_items 和 memory_candidates。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "tier": tier,
                            "existing_summary": safe_existing,
                            "messages": safe_messages,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        payload = _SummaryPayload.model_validate_json(
            response.choices[0].message.content or ""
        )
        output = "\n".join(
            [
                payload.summary,
                *payload.unresolved_items,
                *(item.subject_key for item in payload.memory_candidates),
                *(item.statement for item in payload.memory_candidates),
            ]
        )
        if self._contains_sensitive(output):
            raise ContextSummarySafetyError("摘要输出包含敏感字段")
        allowed_source_ids = {str(item["message_id"]) for item in safe_messages}
        safe_candidates: list[CustomerMemoryCandidate] = []
        for item in payload.memory_candidates:
            candidate_text = f"{item.subject_key} {item.statement}"
            if self._DYNAMIC_MEMORY_PATTERN.search(candidate_text):
                continue
            source_is_visible = item.source_message_id in allowed_source_ids
            safe_candidates.append(
                CustomerMemoryCandidate(
                    subject_key=self._normalize_subject(item.subject_key),
                    category=item.category,
                    statement=item.statement.strip(),
                    evidence_type=(
                        item.evidence_type
                        if source_is_visible
                        else CustomerMemoryEvidenceType.MODEL_INFERENCE
                    ),
                    source_message_id=(
                        item.source_message_id if source_is_visible else None
                    ),
                    confidence=item.confidence,
                    is_correction=item.is_correction,
                )
            )
        return ContextSummaryResult(
            summary=payload.summary,
            unresolved_items=payload.unresolved_items,
            memory_candidates=safe_candidates,
            processed_source_ids=frozenset(allowed_source_ids),
        )

    @classmethod
    def _redact(cls, text: str) -> str:
        """用固定占位符删除本地可识别的敏感片段。"""
        cleaned = text
        for pattern in cls._SENSITIVE_PATTERNS:
            cleaned = pattern.sub("[已脱敏]", cleaned)
        return cleaned

    @classmethod
    def _bound_messages(
        cls, messages: list[MemorySource]
    ) -> list[dict[str, str | bool]]:
        """脱敏后按单条和总字符数限制消息，优先保留最近一批。"""
        cleaned: list[dict[str, str | bool]] = []
        for item in messages:
            if not item.content:
                continue
            cleaned.append(
                {
                    "message_id": item.message_id,
                    "origin": item.origin,
                    "content": cls._redact(item.content)[: cls._MAX_MESSAGE_CHARS],
                    "summary_eligible": item.summary_eligible,
                }
            )
        total = 0
        bounded: list[dict[str, str | bool]] = []
        # 从最新消息向前保留，确保当前偏好和待处理事项不会被旧记录挤掉。
        for entry in reversed(cleaned):
            content = str(entry["content"])
            if total + len(content) > cls._MAX_MESSAGES_CHARS:
                break
            bounded.append(entry)
            total += len(content)
        bounded.reverse()
        return bounded

    @classmethod
    def _contains_sensitive(cls, text: str) -> bool:
        """检查摘要是否包含任何禁止的敏感特征。"""
        return any(pattern.search(text) for pattern in cls._SENSITIVE_PATTERNS)

    @staticmethod
    def _normalize_subject(subject_key: str) -> str:
        """把模型主题键收敛为可比较的稳定小写键。"""
        normalized = re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "_", subject_key.lower())
        return normalized.strip("_")[:128] or "general"
