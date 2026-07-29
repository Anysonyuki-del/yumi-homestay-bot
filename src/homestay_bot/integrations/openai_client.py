import hashlib
import hmac
import json
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field

from homestay_bot.domain.enums import Language
from homestay_bot.services.knowledge_service import KnowledgeService


class ReadOnlyToolExecutor(Protocol):
    """定义模型可以执行的只读业务查询边界。"""

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """执行白名单内的只读查询并返回可序列化结果。"""


class HostexReadOnlyClient(Protocol):
    """定义模型查询所需的最小百居易只读接口。"""

    async def list_properties(self) -> list[Any]:
        """返回全部物理房间。"""

    async def list_availabilities(
        self,
        property_ids: list[int],
        start_date: str,
        end_date: str,
    ) -> list[Any]:
        """返回指定物理房间的日期房态。"""

    async def list_reference_prices(
        self,
        start_date: str,
        end_date: str,
    ) -> list[Any]:
        """返回渠道日历参考价。"""

class HostexReadOnlyToolExecutor:
    """把模型白名单工具映射到百居易只读 API。"""

    def __init__(self, hostex: HostexReadOnlyClient) -> None:
        """注入不包含写入能力的百居易客户端边界。"""
        self._hostex = hostex

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """执行明确允许的查询，并转换为 JSON 可序列化数据。"""
        if name == "search_availability":
            properties = await self._hostex.list_properties()
            result = await self._hostex.list_availabilities(
                [item.id for item in properties],
                arguments["check_in_date"],
                arguments["check_out_date"],
            )
        elif name == "search_reference_price":
            result = await self._hostex.list_reference_prices(
                arguments["check_in_date"],
                arguments["check_out_date"],
            )
        else:
            raise ValueError(f"不允许执行工具: {name}")

        return [item.model_dump(mode="json") for item in result]


class BookingFields(BaseModel):
    """保存模型从对话中提取的非最终预订字段。"""

    check_in_date: str | None = None
    check_out_date: str | None = None
    number_of_guests: int | None = None
    guest_name: str | None = None
    guest_mobile: str | None = None
    room_type_preference: str | None = None
    special_requests: str | None = None


class AssistantDecision(BaseModel):
    """约束模型每轮回复、意图和人工接管决定。"""

    reply_text: str
    language: Language
    intent: str
    confidence: float = Field(ge=0, le=1)
    handoff_reason: str | None = None
    booking_fields: BookingFields | None = None


def assistant_decision_schema() -> dict[str, Any]:
    """返回无引用的严格 Schema，兼容支持 Responses 的第三方端点。"""
    nullable_string = {
        "anyOf": [
            {"type": "string"},
            {"type": "null"},
        ]
    }
    booking_properties: dict[str, Any] = {
        "check_in_date": nullable_string,
        "check_out_date": nullable_string,
        "number_of_guests": {
            "anyOf": [
                {"type": "integer"},
                {"type": "null"},
            ]
        },
        "guest_name": nullable_string,
        "guest_mobile": nullable_string,
        "room_type_preference": nullable_string,
        "special_requests": nullable_string,
    }
    properties: dict[str, Any] = {
        "reply_text": {"type": "string"},
        "language": {"type": "string", "enum": [item.value for item in Language]},
        "intent": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "handoff_reason": nullable_string,
        "booking_fields": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": booking_properties,
                    "required": list(booking_properties),
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class GuestAssistant:
    """使用审核知识和只读工具生成双语客服决定。"""

    def __init__(
        self,
        *,
        client: Any,
        knowledge: KnowledgeService,
        model: str,
        safety_hmac_key: bytes,
        tool_executor: ReadOnlyToolExecutor | None = None,
    ) -> None:
        """注入 OpenAI 客户端、知识服务和不可逆标识密钥。"""
        self._client = client
        self._knowledge = knowledge
        self._model = model
        self._safety_hmac_key = safety_hmac_key
        self._tool_executor = tool_executor

    def tool_definitions(self) -> list[dict[str, Any]]:
        """只暴露房态、参考价和订单查询，不暴露任何写操作。"""
        return [
            {
                "type": "function",
                "name": "search_availability",
                "description": "查询指定入住和退房日期的物理房间可用性。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "check_in_date": {"type": "string", "format": "date"},
                        "check_out_date": {"type": "string", "format": "date"},
                    },
                    "required": ["check_in_date", "check_out_date"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "search_reference_price",
                "description": "查询渠道日历参考价，结果不是最终成交价。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "check_in_date": {"type": "string", "format": "date"},
                        "check_out_date": {"type": "string", "format": "date"},
                    },
                    "required": ["check_in_date", "check_out_date"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        ]

    def _validate_decision(self, output_text: str) -> AssistantDecision:
        """校验结构化结果，并对低置信度回复强制标记人工接管。"""
        decision = AssistantDecision.model_validate_json(output_text)
        if decision.confidence < 0.7 and decision.handoff_reason is None:
            return decision.model_copy(update={"handoff_reason": "low_confidence"})
        return decision

    @staticmethod
    def _minimize_personal_data(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """仅在明确预订资料阶段保留姓名手机号，其余对话先做本地脱敏。"""
        combined = "\n".join(item.get("content", "") for item in messages)
        booking_context = re.search(
            r"预订|订房|下单|booking|reservation|reserve",
            combined,
            re.IGNORECASE,
        )
        if booking_context:
            return [dict(item) for item in messages]

        minimized: list[dict[str, str]] = []
        for item in messages:
            content = item.get("content", "")
            content = re.sub(
                r"(?<!\d)1[3-9]\d{9}(?!\d)",
                "[手机号已隐藏]",
                content,
            )
            content = re.sub(
                r"(?:我叫|姓名(?:是|[:：])?)\s*[\u4e00-\u9fff]{2,4}",
                "[姓名已隐藏]",
                content,
            )
            content = re.sub(
                r"(?i)\bmy name is\s+[a-z][a-z .'-]{0,50}",
                "[name redacted]",
                content,
            )
            minimized.append({**item, "content": content})
        return minimized

    @staticmethod
    def _serialize_output_item(item: Any) -> dict[str, Any]:
        """把 Responses 输出项转为下一轮可重放的无状态输入。"""
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            return dict(model_dump(mode="json", exclude_none=True))
        if getattr(item, "type", None) == "function_call":
            return {
                "type": "function_call",
                "name": item.name,
                "arguments": item.arguments,
                "call_id": item.call_id,
            }
        raise RuntimeError("无法序列化 OpenAI Responses 输出项")

    async def respond(
        self,
        *,
        guest_identifier: str,
        language: Language,
        messages: list[dict[str, str]],
    ) -> AssistantDecision:
        """关闭 OpenAI 状态存储，并返回经过结构校验的客服决定。"""
        knowledge = await self._knowledge.build_context(language)
        knowledge_payload = [
            {
                "source_id": item.source_id,
                "category": item.category,
                "question": item.question,
                "answer": item.answer,
            }
            for item in knowledge
        ]
        system_prompt = (
            "你是武汉一家7间房民宿的客服。只能依据审核知识和查询工具回答。"
            "不得确认最终价格、收款、具体房间、退款、取消或改期；"
            "缺少入住或退房日期时应直接追问，不得仅因此转人工；"
            "只有问题超出审核知识和工具能力、政策不明确、置信度低，"
            "或客人明确要求人工时，才设置 handoff_reason。"
            "只有客人明确确认了入住日期、退房日期、人数、姓名、手机号和房型偏好后，"
            "intent 才能设为 booking_confirmed，并填写全部 booking_fields；"
            f"\n审核知识：{json.dumps(knowledge_payload, ensure_ascii=False)}"
        )
        safety_identifier = hmac.new(
            self._safety_hmac_key,
            guest_identifier.encode(),
            hashlib.sha256,
        ).hexdigest()
        request = {
            "model": self._model,
            "reasoning": {"effort": "low"},
            "store": False,
            "safety_identifier": safety_identifier,
            "instructions": system_prompt,
            "input": self._minimize_personal_data(messages),
            "tools": self.tool_definitions(),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "assistant_decision",
                    "strict": True,
                    "schema": assistant_decision_schema(),
                }
            },
        }
        response = await self._client.responses.create(**request)

        # Responses API 可能连续提出多个只读查询；每轮都显式回传工具结果。
        for _ in range(4):
            function_calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
            if not function_calls:
                return self._validate_decision(response.output_text)
            if self._tool_executor is None:
                raise RuntimeError("模型请求了查询工具，但系统未配置工具执行器")

            replayed_output = [
                self._serialize_output_item(item) for item in response.output
            ]
            outputs: list[dict[str, str]] = []
            for call in function_calls:
                arguments = json.loads(call.arguments)
                result = await self._tool_executor.execute(call.name, arguments)
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            continuation_request = {
                **request,
                # store=False 时显式重放上一轮输出，避免依赖服务端状态。
                "input": [*replayed_output, *outputs],
            }
            response = await self._client.responses.create(
                **continuation_request,
            )

        raise RuntimeError("模型工具调用超过安全轮次限制")
