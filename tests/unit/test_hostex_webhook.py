import httpx
import pytest
from fastapi import FastAPI

from homestay_bot.routes.hostex_webhook import (
    HostexWebhookService,
    get_hostex_webhook_service,
    router,
)


class EventRecorderStub:
    """记录 Webhook 事件与后台任务的原子写入参数。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[dict[str, object]] = []

    async def record_hostex_event(self, **kwargs) -> bool:
        """记录事件；首次返回已创建。"""
        self.calls.append(kwargs)
        return len(self.calls) == 1


@pytest.mark.asyncio
async def test_hostex_webhook_verifies_secret_and_enqueues_once() -> None:
    """合法 Webhook 应快速持久化事件，重复投递不得重复入队。"""
    recorder = EventRecorderStub()
    service = HostexWebhookService("valid", recorder)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_hostex_webhook_service] = lambda: service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/webhooks/hostex",
            headers={"Hostex-Webhook-Secret-Token": "valid"},
            json={
                "event": "reservation_updated",
                "reservation_code": "R-1",
                "guest_phone": "13800138000",
            },
        )
        second = await client.post(
            "/webhooks/hostex",
            headers={"Hostex-Webhook-Secret-Token": "valid"},
            json={
                "event": "reservation_updated",
                "reservation_code": "R-1",
                "guest_phone": "13800138000",
            },
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert recorder.calls[0]["reservation_code"] == "R-1"
    assert recorder.calls[0]["event_key"] == recorder.calls[1]["event_key"]
    assert "guest_phone" not in recorder.calls[0]["payload"]


@pytest.mark.asyncio
async def test_hostex_webhook_rejects_wrong_secret() -> None:
    """错误 Secret 必须返回 401 且不能写入事件。"""
    recorder = EventRecorderStub()
    service = HostexWebhookService("valid", recorder)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_hostex_webhook_service] = lambda: service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/hostex",
            headers={"Hostex-Webhook-Secret-Token": "wrong"},
            json={"event": "reservation_updated", "reservation_code": "R-1"},
        )

    assert response.status_code == 401
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_hostex_webhook_rejects_body_over_limit_before_json_parse() -> None:
    """超过上限的百居易请求必须在 JSON 解析前拒绝。"""
    recorder = EventRecorderStub()
    service = HostexWebhookService("valid", recorder)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_hostex_webhook_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    body = b"x" * (1024 * 1024 + 1)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/hostex",
            headers={"Hostex-Webhook-Secret-Token": "valid"},
            content=body,
        )

    assert response.status_code == 413
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_hostex_webhook_rejects_excessive_json_depth() -> None:
    """合法大小但嵌套过深的 JSON 必须在业务落库前拒绝。"""
    recorder = EventRecorderStub()
    service = HostexWebhookService("valid", recorder)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_hostex_webhook_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    nested: dict[str, object] = {"value": "leaf"}
    for _ in range(20):
        nested = {"child": nested}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/hostex",
            headers={"Hostex-Webhook-Secret-Token": "valid"},
            json={"event": "reservation_updated", "data": nested},
        )

    assert response.status_code == 422
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_hostex_webhook_rejects_oversized_event_fields() -> None:
    """事件类型和业务编号必须在数据库字段上限内提前拒绝。"""
    recorder = EventRecorderStub()
    service = HostexWebhookService("valid", recorder)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_hostex_webhook_service] = lambda: service
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/hostex",
            headers={"Hostex-Webhook-Secret-Token": "valid"},
            json={"event": "x" * 65, "reservation_code": "R-1"},
        )

    assert response.status_code == 422
    assert recorder.calls == []
