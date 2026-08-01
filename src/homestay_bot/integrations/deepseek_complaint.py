import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ComplaintDraftUnavailableError(RuntimeError):
    """表示模型未能生成可供人工审核的客诉草稿。"""


class ComplaintDraft(BaseModel):
    """保存脱敏客诉分析和人工回复草稿。"""

    core_issue: str = Field(min_length=1, max_length=500)
    customer_request: str = Field(min_length=1, max_length=500)
    emotion_level: str = Field(min_length=1, max_length=32)
    customer_claims: list[str] = Field(default_factory=list, max_length=10)
    known_facts: list[str] = Field(default_factory=list, max_length=10)
    facts_to_verify: list[str] = Field(default_factory=list, max_length=10)
    responsibility_risk: str = Field(min_length=1, max_length=64)
    refund_or_compensation: bool = False
    platform_escalation_risk: bool = False
    reply_tone: str = Field(min_length=1, max_length=200)
    reply_draft: str = Field(min_length=1, max_length=1500)

    @field_validator(
        "core_issue",
        "customer_request",
        "emotion_level",
        "responsibility_risk",
        "reply_tone",
        "reply_draft",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        """清理模型字段首尾空白。"""
        return value.strip()

    @field_validator("customer_claims", "known_facts", "facts_to_verify")
    @classmethod
    def _clean_items(cls, values: list[str]) -> list[str]:
        """限制分析列表长度并稳定去重。"""
        return list(dict.fromkeys(item.strip()[:500] for item in values if item.strip()))


class DeepSeekComplaintAnalyzer:
    """调用 DeepSeek 生成仅供人工审核的客诉分析。"""

    _forbidden_commitment = re.compile(
        r"(?:肯定|一定|必须由民宿|民宿有责任|我们负责).{0,20}"
        r"(?:退款|赔偿|赔付|退还)|"
        r"(?:退款|赔偿|赔付).{0,20}\d+(?:元|块)",
        re.IGNORECASE,
    )
    _link = re.compile(r"https?://|\[[^\]]+\]\([^)]+\)", re.IGNORECASE)

    def __init__(self, *, client: Any, model: str) -> None:
        """注入 OpenAI 兼容客户端和模型名称。"""
        self._client = client
        self._model = model

    @classmethod
    def _validate_safety(cls, draft: ComplaintDraft) -> None:
        """拒绝链接、责任结论和金额承诺。"""
        serialized = json.dumps(draft.model_dump(), ensure_ascii=False)
        if cls._link.search(serialized) or cls._forbidden_commitment.search(
            draft.reply_draft
        ):
            raise ComplaintDraftUnavailableError()

    async def generate(
        self,
        *,
        reason: str,
        risk_level: str,
        messages: list[dict[str, str]],
        customer_context: dict[str, Any],
    ) -> ComplaintDraft:
        """使用脱敏消息和客户摘要生成结构化客诉草稿。"""
        system_prompt = (
            "你是武汉民宿的客诉辅助分析员，只为人工管家整理事实和生成回复草稿。"
            "严格区分客人陈述、系统已知事实和待核实事项。不得判断民宿责任，"
            "不得承诺退款、赔偿、折扣或金额，不得引用其他民宿经验，不得输出网址。"
            "回复草稿要冷静、克制、先承认不便并说明正在核实，退款和赔偿必须交由管家确认。"
            "不要输出客人姓名、手机号、外部联系人 ID 或完整订单号。"
            "只输出 JSON，字段为 core_issue、customer_request、emotion_level、"
            "customer_claims、known_facts、facts_to_verify、responsibility_risk、"
            "refund_or_compensation、platform_escalation_risk、reply_tone、reply_draft。"
        )
        payload = {
            "reason": reason[:64],
            "risk_level": risk_level[:32],
            "messages": messages[-12:],
            "customer_context": customer_context,
        }
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                max_tokens=1400,
            )
            draft = ComplaintDraft.model_validate_json(
                response.choices[0].message.content or ""
            )
            self._validate_safety(draft)
            return draft
        except ComplaintDraftUnavailableError:
            raise
        except Exception as error:
            raise ComplaintDraftUnavailableError() from error
