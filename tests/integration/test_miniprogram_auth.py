"""小程序登录 HTTP 集成：真实认证服务、真实仓储、临时 SQLite，只把微信换成 MockTransport。"""
import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import CustomerIdentityProvider, EmployeeRole
from homestay_bot.domain.models import (
    Base,
    Customer,
    CustomerIdentity,
    CustomerMergeSuggestion,
    Employee,
    MiniProgramSession,
)
from homestay_bot.integrations.wechat_miniprogram import (
    MiniProgramCredentials,
    WeChatMiniProgramClient,
)
from homestay_bot.middleware import AdminNoStoreMiddleware
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.routes.health import router as health_router
from homestay_bot.routes.miniprogram_auth import router as miniprogram_router
from homestay_bot.services import miniprogram_auth
from homestay_bot.services.miniprogram_auth import MiniProgramAuthService, MiniProgramLoginGate

APP_ID = "wx0123456789abcdef"
OTHER_APP_ID = "wxfedcba9876543210"
SESSION_KEY = "SYNTHETIC-SESSION-KEY"


@dataclass
class FakeWeChat:
    """按 js_code 返回预设响应，记录每次请求，模拟微信 code2Session。"""

    responses: dict[str, dict[str, object]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        """未登记的 code 按 40029 处理；成功响应带上 session_key 以检查它不外泄。"""
        self.requests.append(request)
        code = request.url.params["js_code"]
        payload = self.responses.get(code, {"errcode": 40029, "errmsg": "invalid code"})
        return httpx.Response(200, content=json.dumps(payload).encode())

    def openid(self, code: str, openid: str) -> None:
        """登记一个成功换取 openid 的 code。"""
        self.responses[code] = {"openid": openid, "session_key": SESSION_KEY}


@dataclass
class Harness:
    """一次测试的应用、数据库与时钟。"""

    app: FastAPI
    factory: async_sessionmaker[AsyncSession]
    wechat: FakeWeChat
    now: list[datetime]

    def service(
        self,
        app_id: str = APP_ID,
        gate: MiniProgramLoginGate | None = None,
    ) -> MiniProgramAuthService:
        """按指定 AppID 构造认证服务：真实服务与仓储，但不经过 application_lifespan 装配。"""
        return MiniProgramAuthService(
            self.factory,
            exchanger=WeChatMiniProgramClient(
                MiniProgramCredentials(app_id=app_id, app_secret="SYNTHETIC-APP-SECRET"),
                transport=httpx.MockTransport(self.wechat.handler),
            ),
            gate=gate,
            clock=lambda: self.now[0],
        )

    def client(self) -> httpx.AsyncClient:
        """返回直连 ASGI 应用的 HTTP 客户端。"""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://testserver",
        )


def _build_app() -> FastAPI:
    """组装与 main.py 相同的中间件与路由（不含生命周期）。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="x" * 32)
    app.add_middleware(AdminNoStoreMiddleware)
    app.include_router(miniprogram_router)
    app.include_router(health_router)
    return app


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    """创建临时 SQLite 与装配好认证服务的应用。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'miniprogram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    result = Harness(
        app=_build_app(),
        factory=create_session_factory(engine),
        wechat=FakeWeChat(),
        now=[datetime(2026, 10, 29, 12, 0, tzinfo=UTC)],
    )
    result.app.state.miniprogram_auth_service = result.service()
    yield result
    await engine.dispose()


async def _login(client: httpx.AsyncClient, code: str) -> httpx.Response:
    """以小程序客户端的形态提交登录请求。"""
    return await client.post("/api/miniprogram/login", json={"code": code})


def _bearer(token: str) -> dict[str, str]:
    """构造 Bearer 请求头。"""
    return {"Authorization": f"Bearer {token}"}


async def _count(factory: async_sessionmaker[AsyncSession], model: type) -> int:
    """统计某张表的行数。"""
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_login_issues_minimal_uncached_session_and_stores_only_digest(
    harness: Harness,
) -> None:
    """登录返回最小字段且禁止缓存；数据库只有摘要，身份以 AppID 命名空间建档。"""
    harness.wechat.openid("code-a", "openid-a")
    async with harness.client() as client:
        response = await _login(client, "code-a")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"token", "expires_at", "display_name"}
    assert body["display_name"] == "小程序客户"
    assert datetime.fromisoformat(body["expires_at"]) == harness.now[0] + timedelta(hours=24)
    assert response.headers["cache-control"] == "no-store"
    assert "openid-a" not in response.text and SESSION_KEY not in response.text

    request = harness.wechat.requests[0]
    assert request.method == "GET" and request.url.path == "/sns/jscode2session"
    assert dict(request.url.params) == {
        "appid": APP_ID,
        "secret": "SYNTHETIC-APP-SECRET",
        "js_code": "code-a",
        "grant_type": "authorization_code",
    }

    async with harness.factory() as session:
        stored = (await session.scalars(select(MiniProgramSession))).all()
        identity = await session.scalar(select(CustomerIdentity))
    assert [row.token_hash for row in stored] == [
        hashlib.sha256(body["token"].encode()).hexdigest()
    ]
    assert identity is not None
    assert identity.provider is CustomerIdentityProvider.WECHAT_MINIPROGRAM
    assert identity.external_id == f"{APP_ID}:openid-a"
    assert identity.is_verified is True


async def test_same_openid_reuses_customer_and_app_id_namespaces_identity(
    harness: Harness,
) -> None:
    """同一 AppID 下同一 OpenID 复用客户；不同 OpenID、不同 AppID 的同名 OpenID 都隔离。"""
    for code in ("c1", "c2", "c3"):
        harness.wechat.openid(code, "openid-same")
    harness.wechat.openid("c4", "openid-other")
    async with harness.client() as client:
        assert (await _login(client, "c1")).status_code == 200
        assert (await _login(client, "c2")).status_code == 200
        assert (await _login(client, "c4")).status_code == 200
        harness.app.state.miniprogram_auth_service = harness.service(OTHER_APP_ID)
        assert (await _login(client, "c3")).status_code == 200

    async with harness.factory() as session:
        external_ids = sorted((await session.scalars(select(CustomerIdentity.external_id))).all())
    assert external_ids == [
        f"{APP_ID}:openid-other",
        f"{APP_ID}:openid-same",
        f"{OTHER_APP_ID}:openid-same",
    ]
    assert await _count(harness.factory, Customer) == 3


async def test_concurrent_first_logins_create_one_customer(harness: Harness) -> None:
    """同一人并发首次登录只建一份档案，两个 token 都可用。"""
    harness.wechat.openid("p1", "openid-race")
    harness.wechat.openid("p2", "openid-race")
    async with harness.client() as client:
        first, second = await asyncio.gather(_login(client, "p1"), _login(client, "p2"))
        assert first.status_code == second.status_code == 200
        for response in (first, second):
            me = await client.get("/api/miniprogram/me", headers=_bearer(response.json()["token"]))
            assert me.status_code == 200
    assert await _count(harness.factory, Customer) == 1


async def test_restore_logout_and_repeat_logout(harness: Harness) -> None:
    """恢复可用；退出后旧 token 失效；重复退出仍是 204 且没有副作用。"""
    harness.wechat.openid("code-b", "openid-b")
    async with harness.client() as client:
        token = (await _login(client, "code-b")).json()["token"]
        me = await client.get("/api/miniprogram/me", headers=_bearer(token))
        assert me.status_code == 200
        assert me.json() == {
            "expires_at": (harness.now[0] + timedelta(hours=24)).isoformat(),
            "display_name": "小程序客户",
        }
        logout = await client.post("/api/miniprogram/logout", headers=_bearer(token))
        assert logout.status_code == 204
        assert (await client.get("/api/miniprogram/me", headers=_bearer(token))).status_code == 401
        repeat = await client.post("/api/miniprogram/logout", headers=_bearer(token))
        assert repeat.status_code == 204
    assert await _count(harness.factory, MiniProgramSession) == 0


async def test_session_expires_after_24_hours_without_renewal(harness: Harness) -> None:
    """固定 24 小时到期：到期前一秒可用，到期即 401，期间访问不会续期。"""
    harness.wechat.openid("code-e", "openid-e")
    async with harness.client() as client:
        token = (await _login(client, "code-e")).json()["token"]
        harness.now[0] += timedelta(hours=24) - timedelta(seconds=1)
        assert (await client.get("/api/miniprogram/me", headers=_bearer(token))).status_code == 200
        harness.now[0] += timedelta(seconds=1)
        assert (await client.get("/api/miniprogram/me", headers=_bearer(token))).status_code == 401


async def test_per_identity_cap_drops_oldest_session(harness: Harness) -> None:
    """同一身份第 6 次登录时删除最早的会话，只保留 5 条。"""
    tokens = []
    async with harness.client() as client:
        for index in range(6):
            harness.wechat.openid(f"cap-{index}", "openid-cap")
            tokens.append((await _login(client, f"cap-{index}")).json()["token"])
        statuses = [
            (await client.get("/api/miniprogram/me", headers=_bearer(token))).status_code
            for token in tokens
        ]
    assert statuses == [401, 200, 200, 200, 200, 200]
    assert await _count(harness.factory, MiniProgramSession) == 5


async def test_login_cleans_expired_sessions_in_bounded_batches(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功登录顺带清理全局过期会话，单次清理数量受上限约束。"""
    monkeypatch.setattr(miniprogram_auth, "EXPIRED_CLEANUP_LIMIT", 2)
    async with harness.factory() as session, session.begin():
        await SQLAlchemyCustomerRepository(session).ensure_identity(
            provider=CustomerIdentityProvider.WECHAT_MINIPROGRAM,
            external_id=f"{APP_ID}:openid-stale",
            display_name="小程序客户",
        )
        identity_id = await session.scalar(select(CustomerIdentity.id))
        assert identity_id is not None
        session.add_all(
            MiniProgramSession(
                token_hash=f"{index:064x}",
                identity_id=identity_id,
                expires_at=harness.now[0] - timedelta(minutes=index + 1),
            )
            for index in range(3)
        )
    harness.wechat.openid("code-c", "openid-fresh")
    async with harness.client() as client:
        assert (await _login(client, "code-c")).status_code == 200
    # 3 条过期中清掉 2 条，加上新签发的 1 条。
    assert await _count(harness.factory, MiniProgramSession) == 2


async def test_session_follows_manual_customer_merge(harness: Harness) -> None:
    """管理员合并客户后原 token 仍可用（解析到合并后的客户），且不显示合并目标的姓名。"""
    harness.wechat.openid("code-m", "openid-m")
    async with harness.client() as client:
        token = (await _login(client, "code-m")).json()["token"]
        async with harness.factory() as session:
            source = await session.scalar(select(Customer))
            assert source is not None
            target = Customer(display_name="订单客户张")
            admin = Employee(wecom_userid="admin-merge", name="YuMi", role=EmployeeRole.ADMIN)
            session.add_all([target, admin])
            await session.flush()
            suggestion = CustomerMergeSuggestion(
                source_customer_id=source.id,
                target_customer_id=target.id,
                reason="verified_phone",
            )
            session.add(suggestion)
            await session.commit()
            await SQLAlchemyCustomerRepository(session).merge_locked(suggestion.id, admin.id)
            await session.commit()
        me = await client.get("/api/miniprogram/me", headers=_bearer(token))
    # resolve 只接受未被合并的客户：仍返回 200 说明会话已随身份指向合并后的客户。
    # 名称固定为通用名，不暴露合并目标的姓名（用户 2026-10-30 决定）。
    assert me.status_code == 200
    assert me.json()["display_name"] == "小程序客户"
    assert "订单客户张" not in me.text


async def test_failed_session_write_rolls_back_new_customer(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """签发阶段失败时整笔回滚：不返回 token，也不留下新建的客户或身份。"""

    async def fail_create(*_: object, **__: object) -> None:
        """模拟会话写入失败。"""
        raise RuntimeError("synthetic write failure")

    monkeypatch.setattr(
        miniprogram_auth.SQLAlchemyMiniProgramSessionRepository, "create", fail_create
    )
    harness.wechat.openid("code-f", "openid-f")
    async with harness.client() as client:
        response = await _login(client, "code-f")
    assert response.status_code == 503
    assert response.json()["error"] == "unavailable"
    assert await _count(harness.factory, Customer) == 0
    assert await _count(harness.factory, CustomerIdentity) == 0


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        json.dumps({"code": "has space"}).encode(),
        json.dumps({"code": "x" * 129}).encode(),
        json.dumps({"code": 123}).encode(),
        json.dumps({"code": "SECRET-ECHO", "pad": "y" * 1100}).encode(),
    ],
)
async def test_invalid_login_input_is_rejected_without_echo_or_upstream_call(
    harness: Harness,
    body: bytes,
) -> None:
    """无效或超长输入返回 400，不回显内容，也不调用微信。"""
    async with harness.client() as client:
        response = await client.post(
            "/api/miniprogram/login",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "SECRET-ECHO" not in response.text and "has space" not in response.text
    assert harness.wechat.requests == []


@pytest.mark.parametrize(
    ("errcode", "status_code", "error"),
    [
        (40029, 401, "code_invalid"),
        (40226, 403, "login_blocked"),
        (45011, 503, "upstream_busy"),
        (-1, 503, "upstream_busy"),
        (40163, 503, "upstream_unavailable"),
    ],
)
async def test_wechat_errors_map_to_safe_responses_without_session(
    harness: Harness,
    errcode: int,
    status_code: int,
    error: str,
) -> None:
    """微信错误按表映射，不透传 errmsg，不建档也不签发会话。"""
    harness.wechat.responses["bad"] = {"errcode": errcode, "errmsg": "UPSTREAM-ERRMSG"}
    async with harness.client() as client:
        response = await _login(client, "bad")
    assert response.status_code == status_code
    assert response.json()["error"] == error
    assert "UPSTREAM-ERRMSG" not in response.text
    assert len(harness.wechat.requests) == 1
    assert await _count(harness.factory, Customer) == 0


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer short",
        "Basic " + "a" * 43,
        "Bearer " + "a" * 43 + " extra",
        "Bearer " + "a" * 300,
    ],
)
async def test_me_rejects_missing_or_malformed_bearer(
    harness: Harness,
    authorization: str | None,
) -> None:
    """缺失、格式不符或超长的 Authorization 一律 401。"""
    headers = {} if authorization is None else {"Authorization": authorization}
    async with harness.client() as client:
        response = await client.get("/api/miniprogram/me", headers=headers)
    assert response.status_code == 401


async def test_guest_token_and_employee_session_are_not_interchangeable(
    harness: Harness,
) -> None:
    """客人 token 不能访问员工接口；只有员工 Cookie 也不能通过小程序认证。"""
    harness.wechat.openid("code-x", "openid-x")
    async with harness.client() as client:
        token = (await _login(client, "code-x")).json()["token"]
        employee = await client.get("/employee/health", headers=_bearer(token))
        assert employee.status_code in {401, 303, 307}
        client.cookies.set("session", "forged-employee-cookie")
        assert (await client.get("/api/miniprogram/me")).status_code == 401


async def test_global_window_limit_returns_429_without_upstream_call(harness: Harness) -> None:
    """窗口额度用完后直接 429，不再调用微信。"""
    harness.app.state.miniprogram_auth_service = harness.service(
        gate=MiniProgramLoginGate(window_limit=2)
    )
    async with harness.client() as client:
        statuses = [(await _login(client, f"w{index}")).status_code for index in range(3)]
    assert statuses == [401, 401, 429]
    assert len(harness.wechat.requests) == 2


async def test_in_flight_limit_rejects_concurrent_login(harness: Harness) -> None:
    """在途名额占满时并发的登录立即 429，不排队；名额释放后恢复。"""
    release = asyncio.Event()
    entered = asyncio.Event()

    class SlowExchanger:
        """第一次调用阻塞到测试放行，用于占住在途名额。"""

        app_id = APP_ID

        async def exchange_code(self, code: str) -> str:
            """等待放行后返回固定 openid。"""
            entered.set()
            await release.wait()
            return "openid-slow"

    harness.app.state.miniprogram_auth_service = MiniProgramAuthService(
        harness.factory,
        exchanger=SlowExchanger(),
        gate=MiniProgramLoginGate(max_in_flight=1),
        clock=lambda: harness.now[0],
    )
    async with harness.client() as client:
        first = asyncio.create_task(_login(client, "slow-1"))
        await entered.wait()
        second = await _login(client, "slow-2")
        release.set()
        assert (await first).status_code == 200
        third = await _login(client, "slow-3")
    assert second.status_code == 429
    assert third.status_code == 200


async def test_unconfigured_service_only_disables_miniprogram_endpoints(
    harness: Harness,
) -> None:
    """未装配认证服务时三项接口都返回 503，公开健康接口照常响应。"""
    del harness.app.state.miniprogram_auth_service
    async with harness.client() as client:
        responses = [
            await _login(client, "any"),
            await client.get("/api/miniprogram/me", headers=_bearer("a" * 43)),
            await client.post("/api/miniprogram/logout", headers=_bearer("a" * 43)),
        ]
        health = await client.get("/health")
    assert [response.status_code for response in responses] == [503, 503, 503]
    assert all(response.json()["error"] == "not_configured" for response in responses)
    assert health.status_code in {200, 503} and health.json()["status"] in {"ok", "degraded"}


async def test_local_database_failure_log_omits_openid_parameters(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """数据库异常正文带 SQL 参数（含 AppID:OpenID）；日志只记类型与位置，不得出现 OpenID。"""

    async def fail_identity(*_: object, **__: object) -> None:
        """模拟写身份时的数据库故障，异常参数里带着 external_id。"""
        raise OperationalError(
            "INSERT INTO customer_identities ...",
            (f"{APP_ID}:openid-leak-check",),
            Exception("disk I/O error"),
        )

    monkeypatch.setattr(
        miniprogram_auth.SQLAlchemyCustomerRepository, "ensure_identity", fail_identity
    )
    harness.wechat.openid("code-l", "openid-leak-check")
    caplog.set_level(logging.DEBUG)
    async with harness.client() as client:
        response = await _login(client, "code-l")
    assert response.status_code == 503
    assert "openid-leak-check" not in caplog.text
    assert "stage=login error=OperationalError" in caplog.text
