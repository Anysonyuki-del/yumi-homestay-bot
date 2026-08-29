import json
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
from homestay_bot.services.customer_memory_policy import (
    contains_sensitive_memory_text,
    is_dynamic_memory_text,
    is_instruction_like_memory,
    normalize_subject_key,
    redact_memory_text,
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
    source_excerpt: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    is_correction: bool = False


class _SummaryPayload(BaseModel):
    """校验 DeepSeek 分层摘要的固定 JSON 结构。"""

    summary: str = Field(min_length=1, max_length=2000)
    memory_candidates: list[_MemoryCandidatePayload] = Field(
        default_factory=list, max_length=10
    )


class DeepSeekContextSummarizer:
    """先本地脱敏，再使用 DeepSeek 合并客户分层摘要。"""

    # 限制单条和总消息输入，避免高频会话耗尽模型上下文或请求预算。
    _MAX_MESSAGE_CHARS = 2_000
    _MAX_MESSAGES_CHARS = 6_000
    _MAX_EXISTING_SUMMARY_CHARS = 2_000
    _MAX_OUTPUT_TOKENS = 1_200

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
        safe_existing = redact_memory_text(existing_summary)[
            : self._MAX_EXISTING_SUMMARY_CHARS
        ]
        safe_messages = self._bound_messages(messages)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你负责整理民宿客户上下文。只保留可跨会话复用的情节、稳定偏好和"
                        "已确认事实；运营待办不得进入摘要。"
                        "禁止输出手机号、身份证、详细地址、门锁密码、验证码或二维码。"
                        "价格、房态、付款退款、当前订单状态和临时承诺不得成为记忆。"
                        "memory_candidates 仅提取可跨会话复用的稳定客户事实；"
                        "summary 只能合并 summary_eligible=true 的消息；该字段为 false"
                        "的最近原文只用于提取 memory_candidates，不得写入 summary。"
                        "如果没有可进入 summary 的消息，保持 existing_summary；它为空时"
                        "令 summary 为“无新增摘要”。"
                        "subject_key 使用稳定简短的 snake_case 主题；source_message_id 必须"
                        "引用输入消息编号；source_excerpt 必须逐字引用该消息中能证明候选的"
                        "脱敏连续原文，不能改写或概括；无法证明为客户明示或员工确认时 evidence_type"
                        "必须为 model_inference。客户明确纠正同一主题时 is_correction=true。"
                        "不要生成任何待办字段；只输出 JSON：summary 和"
                        "memory_candidates。memory_candidates 最多 10 条。"
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
            max_tokens=self._MAX_OUTPUT_TOKENS,
            extra_body={"thinking": {"type": "disabled"}},
        )
        payload = _SummaryPayload.model_validate_json(
            response.choices[0].message.content or ""
        )
        output = "\n".join(
            [
                payload.summary,
                *(item.subject_key for item in payload.memory_candidates),
                *(item.statement for item in payload.memory_candidates),
                *(item.source_excerpt for item in payload.memory_candidates),
            ]
        )
        if contains_sensitive_memory_text(output):
            raise ContextSummarySafetyError("摘要输出包含敏感字段")
        allowed_source_ids = {str(item["message_id"]) for item in safe_messages}
        safe_candidates: list[CustomerMemoryCandidate] = []
        for item in payload.memory_candidates:
            candidate_text = f"{item.subject_key} {item.statement}"
            if is_dynamic_memory_text(candidate_text) or is_instruction_like_memory(
                f"{candidate_text} {item.source_excerpt}"
            ):
                continue
            source_is_visible = item.source_message_id in allowed_source_ids
            safe_candidates.append(
                CustomerMemoryCandidate(
                    subject_key=normalize_subject_key(item.subject_key),
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
                    source_excerpt=item.source_excerpt.strip(),
                )
            )
        return ContextSummaryResult(
            summary=payload.summary,
            unresolved_items=[],
            memory_candidates=safe_candidates,
            processed_source_ids=frozenset(allowed_source_ids),
        )

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
                    "content": redact_memory_text(item.content)[: cls._MAX_MESSAGE_CHARS],
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
