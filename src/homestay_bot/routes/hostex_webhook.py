import hashlib
import json
import secrets
from json import JSONDecodeError
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

router = APIRouter()

# 百居易事件允许携带较多字段，但仍需限制认证前的请求体内存占用。
HOSTEX_WEBHOOK_MAX_BODY_BYTES = 1024 * 1024
HOSTEX_WEBHOOK_MAX_JSON_DEPTH = 16
HOSTEX_WEBHOOK_MAX_JSON_NODES = 5000
HOSTEX_WEBHOOK_MAX_STRING_CHARS = 16_384


def _validate_json_shape(value: Any) -> None:
    """迭代限制 JSON 深度、节点数、键名和字符串长度。"""
    stack: list[tuple[Any, int]] = [(value, 1)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if depth > HOSTEX_WEBHOOK_MAX_JSON_DEPTH:
            raise ValueError("百居易 Webhook JSON 嵌套过深")
        if visited > HOSTEX_WEBHOOK_MAX_JSON_NODES:
            raise ValueError("百居易 Webhook JSON 字段过多")
        if isinstance(current, dict):
            for key, item in current.items():
                if len(str(key)) > 256:
                    raise ValueError("百居易 Webhook JSON 字段名过长")
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, str) and len(current) > HOSTEX_WEBHOOK_MAX_STRING_CHARS:
            raise ValueError("百居易 Webhook JSON 字符串过长")


def _bounded_field(value: Any, *, name: str, max_length: int) -> str:
    """把 Webhook 标量转为字符串并校验数据库字段长度。"""
    normalized = str(value)
    if len(normalized) > max_length:
        raise ValueError(f"百居易 Webhook {name}字段过长")
    return normalized


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    """以流式方式读取请求体，超过上限时立即返回 413。"""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="百居易 Webhook 请求体过大",
            )

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="百居易 Webhook 请求体过大",
            )
        chunks.append(chunk)
    return b"".join(chunks)


class HostexEventRecorder(Protocol):
    """定义事件和后台任务原子落库接口。"""

    async def record_hostex_event(
        self,
        *,
        event_key: str,
        event_type: str,
        reservation_code: str | None,
        payload: dict[str, Any],
    ) -> bool:
        """首次事件返回真，重复事件保持幂等。"""


class HostexWebhookService:
    """校验百居易 Webhook Secret 并生成稳定事件键。"""

    def __init__(self, secret_token: str, recorder: HostexEventRecorder) -> None:
        """注入 Secret 和同事务事件记录器。"""
        self._secret_token = secret_token
        self._recorder = recorder

    def verify_secret(self, secret_token: str) -> None:
        """在读取和解析请求体前校验 Webhook Secret。"""
        if not secrets.compare_digest(secret_token, self._secret_token):
            raise PermissionError("百居易 Webhook Secret 无效")

    async def receive(
        self,
        secret_token: str,
        payload: dict[str, Any],
    ) -> str:
        """验证请求并快速持久化，不调用百居易或 AI。"""
        self.verify_secret(secret_token)
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        supplied_key = payload.get("event_id") or payload.get("id")
        event_key = (
            _bounded_field(supplied_key, name="事件编号", max_length=128)
            if supplied_key
            else hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )
        raw_data = payload.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        reservation_code = payload.get("reservation_code") or data.get(
            "reservation_code"
        )
        event_type = _bounded_field(
            payload.get("event") or payload.get("event_type") or "unknown",
            name="事件类型",
            max_length=64,
        )
        normalized_reservation_code = (
            _bounded_field(
                reservation_code,
                name="订单编号",
                max_length=128,
            )
            if reservation_code is not None
            else None
        )
        # Webhook 原文可能包含客人资料；事件表只保存同步所需白名单字段。
        safe_payload = {
            "event_type": event_type,
            "reservation_code": (
                normalized_reservation_code
            ),
        }
        await self._recorder.record_hostex_event(
            event_key=event_key,
            event_type=event_type,
            reservation_code=normalized_reservation_code,
            payload=safe_payload,
        )
        return event_key


def get_hostex_webhook_service(request: Request) -> HostexWebhookService:
    """从应用状态读取请求级 Webhook 服务。"""
    service: object = getattr(request.app.state, "hostex_webhook_service", None)
    if not isinstance(service, HostexWebhookService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="百居易 Webhook 服务尚未配置",
        )
    return service


HostexWebhookDependency = Annotated[
    HostexWebhookService,
    Depends(get_hostex_webhook_service),
]
HostexSecretHeader = Annotated[
    str,
    Header(alias="Hostex-Webhook-Secret-Token"),
]


@router.post("/webhooks/hostex", status_code=status.HTTP_202_ACCEPTED)
async def receive_hostex_webhook(
    request: Request,
    secret_token: HostexSecretHeader,
    service: HostexWebhookDependency,
) -> dict[str, str]:
    """验证并持久化 Webhook 后立即返回 202。"""
    try:
        # Secret 校验必须先于 JSON 解析，且后续读取仍受请求体上限保护。
        service.verify_secret(secret_token)
        body = await _read_limited_body(request, HOSTEX_WEBHOOK_MAX_BODY_BYTES)
        try:
            payload_value = json.loads(body)
        except (JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="百居易 Webhook 请求体必须是合法 JSON",
            ) from error
        if not isinstance(payload_value, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="百居易 Webhook 请求体必须是 JSON 对象",
            )
        try:
            _validate_json_shape(payload_value)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        payload: dict[str, Any] = payload_value
        event_key = await service.receive(secret_token, payload)
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return {"status": "accepted", "event_key": event_key}
