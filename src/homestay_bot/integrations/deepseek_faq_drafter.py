import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

_LINK_PATTERN = re.compile(r"https?://|\[[^\]]+\]\([^)]+\)", re.IGNORECASE)
_ADMIN_CONFIRMATION_PLACEHOLDER = "【待管理员确认】"
_PROPERTY_SPECIFIC_PATTERN = re.compile(
    r"民宿|本店|你们|你家|停车|车位|收费|费用|数量|设施|政策|"
    r"入住|退房|寄存|宠物|吸烟|电梯|厨房|洗衣|空调|地址|距离|"
    r"homestay|property|parking|fee|facilit|policy|check.?in|"
    r"check.?out|luggage|pet|smoking|elevator|kitchen|laundry|"
    r"address|distance",
    re.IGNORECASE,
)


class FaqDraftUnavailableError(RuntimeError):
    """表示 DeepSeek 未能生成可安全交给管理员的 FAQ 草稿。"""


class FaqDraft(BaseModel):
    """保存管理员可编辑的中英文 FAQ 草稿。"""

    category: str = Field(min_length=1, max_length=64)
    question_zh: str = Field(min_length=1, max_length=300)
    answer_zh: str = Field(min_length=1, max_length=1500)
    question_en: str = Field(min_length=1, max_length=500)
    answer_en: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    verification_items: list[str] = Field(default_factory=list, max_length=8)

    @field_validator(
        "category",
        "question_zh",
        "answer_zh",
        "question_en",
        "answer_en",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        """删除字段首尾空白，避免空壳草稿进入管理页面。"""
        return value.strip()

    @field_validator("keywords", "verification_items")
    @classmethod
    def _clean_list(cls, values: list[str]) -> list[str]:
        """清理、去重并限制列表内单项长度。"""
        cleaned = [item.strip()[:100] for item in values if item.strip()]
        return list(dict.fromkeys(cleaned))


class DeepSeekFaqDrafter:
    """通过 DeepSeek 生成仅供管理员审核的 FAQ 参考草稿。"""

    def __init__(self, *, client: Any, model: str) -> None:
        """注入 OpenAI 兼容客户端和模型名称。"""
        self._client = client
        self._model = model

    @classmethod
    def _validate_safety(
        cls,
        draft: FaqDraft,
        *,
        canonical_question: str,
        category: str,
    ) -> None:
        """拒绝链接和没有待确认占位的未核实专属事实。"""
        serialized = json.dumps(draft.model_dump(), ensure_ascii=False)
        if _LINK_PATTERN.search(serialized):
            raise FaqDraftUnavailableError()
        if (
            draft.verification_items
            and _ADMIN_CONFIRMATION_PLACEHOLDER not in draft.answer_zh
        ):
            raise FaqDraftUnavailableError()
        if cls._is_property_specific_draft(
            draft,
            canonical_question=canonical_question,
            category=category,
        ) and (
            not draft.verification_items
            or _ADMIN_CONFIRMATION_PLACEHOLDER not in draft.answer_zh
        ):
            raise FaqDraftUnavailableError()

    @staticmethod
    def _is_property_specific_draft(
        draft: FaqDraft,
        *,
        canonical_question: str,
        category: str,
    ) -> bool:
        """判断草稿是否涉及必须由管理员最终确认的民宿专属事实。"""
        subject = "\n".join(
            (
                canonical_question,
                category,
                draft.question_zh,
                draft.answer_zh,
                draft.question_en,
                draft.answer_en,
            )
        )
        return _PROPERTY_SPECIFIC_PATTERN.search(subject) is not None

    async def generate(
        self,
        *,
        canonical_question: str,
        category: str,
        examples: list[str],
        approved_knowledge: list[dict[str, str]],
    ) -> FaqDraft:
        """使用脱敏示例和审核知识生成严格 JSON 草稿。"""
        system_prompt = (
            "你是武汉一家7间房民宿的FAQ编辑，只为管理员生成参考草稿。"
            "不得参考其他民宿、行业惯例或常识推测本店事实。"
            "凡涉及本店停车数量、收费、时间、设施或政策等专属事实，"
            "即使审核知识中出现相似内容，也必须在中文答案对应位置写"
            "“【待管理员确认】”，并列入verification_items，由管理员"
            "最终确认。生成完整中英文问答，但不得输出网址、"
            "Markdown链接或客人身份。草稿不会直接回复客人。"
            "只输出JSON，字段为category、question_zh、answer_zh、"
            "question_en、answer_en、keywords、verification_items。"
        )
        payload = {
            "canonical_question": canonical_question.strip(),
            "suggested_category": category.strip(),
            "redacted_examples": examples[:3],
            "approved_knowledge": approved_knowledge[:100],
        }
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                max_tokens=1200,
            )
            content = response.choices[0].message.content or ""
            draft = FaqDraft.model_validate_json(content)
            self._validate_safety(
                draft,
                canonical_question=canonical_question,
                category=category,
            )
            return draft
        except FaqDraftUnavailableError:
            raise
        except Exception as error:
            # 统一为无正文领域异常，禁止把模型响应或审核知识写入日志。
            raise FaqDraftUnavailableError() from error
