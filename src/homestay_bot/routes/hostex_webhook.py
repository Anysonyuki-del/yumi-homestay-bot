import hashlib
import json
import secrets
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

router = APIRouter()


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

    async def receive(
        self,
        secret_token: str,
        payload: dict[str, Any],
    ) -> str:
        """验证请求并快速持久化，不调用百居易或 AI。"""
        if not secrets.compare_digest(secret_token, self._secret_token):
            raise PermissionError("百居易 Webhook Secret 无效")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        supplied_key = payload.get("event_id") or payload.get("id")
        event_key = str(supplied_key) if supplied_key else hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        raw_data = payload.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        reservation_code = payload.get("reservation_code") or data.get(
            "reservation_code"
        )
        event_type = str(
            payload.get("event") or payload.get("event_type") or "unknown"
        )
        # Webhook 原文可能包含客人资料；事件表只保存同步所需白名单字段。
        safe_payload = {
            "event_type": event_type,
            "reservation_code": (
                str(reservation_code) if reservation_code is not None else None
            ),
        }
        await self._recorder.record_hostex_event(
            event_key=event_key,
            event_type=event_type,
            reservation_code=(
                str(reservation_code) if reservation_code is not None else None
            ),
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
    payload: dict[str, Any],
    secret_token: HostexSecretHeader,
    service: HostexWebhookDependency,
) -> dict[str, str]:
    """验证并持久化 Webhook 后立即返回 202。"""
    try:
        event_key = await service.receive(secret_token, payload)
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return {"status": "accepted", "event_key": event_key}
