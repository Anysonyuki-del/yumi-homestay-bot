import re
from types import SimpleNamespace

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.runtime_config import RuntimeConfigView
from homestay_bot.routes.employee_auth import router as auth_router
from homestay_bot.routes.runtime_config import router as runtime_config_router
from homestay_bot.services.admin_auth_service import Argon2CapacityError
from homestay_bot.services.runtime_config_service import (
    ActivationResult,
    RuntimeConfigPage,
    RuntimeConfigUnavailableError,
    UpdateRuntimeConfig,
)


def masked_view() -> RuntimeConfigView:
    """构造不含完整身份和密钥的设置页投影。"""
    return RuntimeConfigView(
        deepseek_api_key="已配置 ····A1B2",
        deepseek_base_url="https://api.deepseek.example",
        deepseek_model="deepseek-v4-flash",
        hostex_access_token="已配置 ····C3D4",
        hostex_webhook_secret_token="已配置 ····E5F6",
        hostex_reconcile_interval_seconds=900.0,
        wecom_corp_id="已配置 ····G7H8",
        wecom_kf_secret="已配置 ····I9J0",
        wecom_callback_token="已配置 ····K1L2",
        wecom_encoding_aes_key="已配置 ····AAAA",
        wecom_agent_id=1000002,
        wecom_agent_secret="已配置 ····M3N4",
        wecom_contact_secret="已配置 ····O5P6",
        wecom_duty_userids="已配置 ····wner",
        wecom_poll_interval_seconds=10.0,
    )


class RuntimeConfigServiceStub:
    """记录设置路由传递的认证、CAS 与更新命令。"""

    def __init__(self) -> None:
        """初始化固定页面状态和调用记录。"""
        self.page = RuntimeConfigPage(
            view=masked_view(),
            revision=6,
            active_version_id=12,
            previous_version_id=11,
            source="database",
        )
        self.activation_calls: list[dict[str, object]] = []
        self.rollback_calls: list[dict[str, object]] = []

    async def page_data(self) -> RuntimeConfigPage:
        """返回固定脱敏页面状态。"""
        return self.page

    async def list_version_views(self, *, limit: int = 20) -> list[object]:
        """返回一条不含密文的历史记录。"""
        return [
            SimpleNamespace(
                version_id=12,
                created_at=None,
                created_by=1,
                is_active=True,
                is_previous=False,
                masked_summary={"deepseek_api_key": "已配置 ····A1B2"},
            )
        ]

    async def create_and_test(self, command: UpdateRuntimeConfig, **fields: object):
        """记录激活参数并返回安全结果。"""
        self.activation_calls.append({"command": command, **fields})
        return ActivationResult(version_id=13, revision=7, view=masked_view())

    async def rollback(self, **fields: object):
        """记录回滚参数并返回安全结果。"""
        self.rollback_calls.append(fields)
        return ActivationResult(version_id=11, revision=7, view=masked_view())


def build_client() -> tuple[TestClient, RuntimeConfigServiceStub]:
    """装配真实认证、服务端 CSRF 与设置路由。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="runtime-route-test-secret")
    app.include_router(auth_router)
    app.include_router(runtime_config_router)
    configure_admin_auth(app, EmployeeRole.ADMIN)
    service = RuntimeConfigServiceStub()
    app.state.runtime_config_service = service
    return TestClient(app), service


def tokens(response_text: str, action: str) -> list[str]:
    """提取指定 POST action 内的全部 CSRF nonce。"""
    return re.findall(
        rf'<form[^>]+action="{re.escape(action)}".*?</form>',
        response_text,
        re.DOTALL,
    ) and re.findall(
        r'name="csrf_token" value="([^"]+)"',
        re.findall(
            rf'<form[^>]+action="{re.escape(action)}".*?</form>',
            response_text,
            re.DOTALL,
        )[0],
    )


def test_settings_requires_admin_and_never_returns_complete_secrets() -> None:
    """匿名用户被拦截，管理员页面只含掩码且禁止缓存。"""
    client, _ = build_client()
    anonymous = client.get(
        "/employee/admin/settings",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert anonymous.status_code == 303
    login_admin(client, next_path="/employee/admin/settings")

    response = client.get("/employee/admin/settings")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "已配置 ····A1B2" in response.text
    for secret in (
        "deepseek-secret-A1B2",
        "hostex-secret-C3D4",
        "corp-G7H8",
        "owner",
    ):
        assert secret not in response.text
    assert "当前进程将在安全切换完成后使用" in response.text


def test_activate_and_rollback_use_separate_multi_tab_server_nonces() -> None:
    """不同动作及两个标签页的 nonce 必须独立，消费一个不覆盖另一个。"""
    client, _ = build_client()
    login_admin(client, next_path="/employee/admin/settings")

    first = client.get("/employee/admin/settings")
    second = client.get("/employee/admin/settings")
    activate_one = tokens(first.text, "/employee/admin/settings/activate")[0]
    activate_two = tokens(second.text, "/employee/admin/settings/activate")[0]
    rollback_one = tokens(first.text, "/employee/admin/settings/rollback")[0]

    assert len({activate_one, activate_two, rollback_one}) == 3
    first_submit = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": activate_one,
            "password": "correct-password",
            "expected_revision": "6",
            "deepseek_model": "new-model",
        },
        follow_redirects=False,
    )
    second_submit = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": activate_two,
            "password": "correct-password",
            "expected_revision": "6",
            "deepseek_model": "newer-model",
        },
        follow_redirects=False,
    )

    assert first_submit.status_code == 303
    assert second_submit.status_code == 303


def test_activate_passes_session_version_cas_and_explicit_contact_clear() -> None:
    """激活路由必须绑定管理员、会话版本、页面 revision 和明确清除动作。"""
    client, service = build_client()
    login_admin(client, next_path="/employee/admin/settings")
    page = client.get("/employee/admin/settings")
    csrf = tokens(page.text, "/employee/admin/settings/activate")[0]

    response = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": csrf,
            "password": "correct-password",
            "expected_revision": "6",
            "deepseek_model": "new-model",
            "clear_wecom_contact_secret": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    call = service.activation_calls[0]
    assert call["actor_id"] == 1
    assert call["admin_id"] == 1
    assert call["expected_session_version"] == 1
    assert call["expected_revision"] == 6
    command = call["command"]
    assert isinstance(command, UpdateRuntimeConfig)
    assert command.clear_wecom_contact_secret is True
    assert command.deepseek_model == "new-model"


def test_rollback_passes_both_page_cas_values_and_replay_is_rejected() -> None:
    """回滚需绑定 revision 与 previous id，已消费 nonce 不可重放。"""
    client, service = build_client()
    login_admin(client, next_path="/employee/admin/settings")
    page = client.get("/employee/admin/settings")
    csrf = tokens(page.text, "/employee/admin/settings/rollback")[0]
    data = {
        "csrf_token": csrf,
        "password": "correct-password",
        "expected_revision": "6",
        "expected_previous_version_id": "11",
    }

    response = client.post(
        "/employee/admin/settings/rollback",
        data=data,
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/admin/settings/rollback",
        data=data,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert replay.status_code == 409
    assert replay.headers["cache-control"] == "no-store"
    assert service.rollback_calls == [
        {
            "actor_id": 1,
            "admin_id": 1,
            "password": "correct-password",
            "expected_session_version": 1,
            "expected_revision": 6,
            "expected_previous_version_id": 11,
        }
    ]


def test_staff_cannot_open_or_submit_runtime_settings() -> None:
    """普通员工即使持有会话也不能读取或修改外部配置。"""
    client, _ = build_client()
    configure_admin_auth(client.app, EmployeeRole.STAFF)
    login_admin(client, next_path="/employee/admin/settings")

    page = client.get("/employee/admin/settings")
    submit = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": "forged",
            "password": "password",
            "expected_revision": "6",
        },
    )

    assert page.status_code == 403
    assert submit.status_code == 403


def test_invalid_long_secret_and_password_are_never_echoed() -> None:
    """表单边界错误不得让框架把完整密钥或密码写回响应。"""
    client, service = build_client()
    login_admin(client, next_path="/employee/admin/settings")
    page = client.get("/employee/admin/settings")
    secret = "secret-sentinel-" + "X" * 5000
    password = "password-sentinel-" + "Y" * 256

    response = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": tokens(page.text, "/employee/admin/settings/activate")[0],
            "password": password,
            "expected_revision": "6",
            "deepseek_api_key": secret,
        },
    )

    assert response.status_code == 200
    assert "输入内容过长" in response.text
    assert secret not in response.text
    assert password not in response.text
    assert service.activation_calls == []


def test_validation_error_does_not_echo_url_and_is_never_cached() -> None:
    """框架级表单错误也不得回显可能带凭据的 URL，且必须禁止缓存。"""
    client, _ = build_client()
    login_admin(client, next_path="/employee/admin/settings")
    page = client.get("/employee/admin/settings")
    url = "https://user:token-sentinel@example.com/" + "x" * 2100

    response = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": tokens(page.text, "/employee/admin/settings/activate")[0],
            "password": "correct-password",
            "expected_revision": "6",
            "deepseek_base_url": url,
        },
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert url not in response.text
    assert "token-sentinel" not in response.text


def test_missing_config_key_keeps_page_readable_but_rejects_writes() -> None:
    """配置主密钥缺失时页面应明确只读，提交返回受控降级响应。"""
    client, service = build_client()
    service.page = RuntimeConfigPage(
        view=masked_view(),
        revision=6,
        active_version_id=12,
        previous_version_id=11,
        source="environment",
        writable=False,
    )

    async def reject_write(*args: object, **kwargs: object) -> object:
        """模拟启动修复模式拒绝加密配置写入。"""
        raise RuntimeConfigUnavailableError("secret-sentinel-must-not-leak")

    service.create_and_test = reject_write  # type: ignore[method-assign]
    login_admin(client, next_path="/employee/admin/settings")
    page = client.get("/employee/admin/settings")
    csrf = tokens(page.text, "/employee/admin/settings/activate")[0]

    response = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": csrf,
            "password": "correct-password",
            "expected_revision": "6",
            "deepseek_model": "new-model",
        },
    )

    assert page.status_code == 200
    assert "当前为只读模式" in page.text
    assert response.status_code == 503
    assert "配置主密钥未就绪" in response.text
    assert "secret-sentinel-must-not-leak" not in response.text


def test_password_verification_capacity_returns_retryable_safe_response() -> None:
    """密码线程池繁忙时应返回 429，且不得记录或回显密码。"""
    client, service = build_client()

    async def reject_capacity(*args: object, **kwargs: object) -> object:
        """模拟 Argon2 有界执行池暂时饱和。"""
        raise Argon2CapacityError("password-sentinel-must-not-leak")

    service.create_and_test = reject_capacity  # type: ignore[method-assign]
    login_admin(client, next_path="/employee/admin/settings")
    page = client.get("/employee/admin/settings")
    response = client.post(
        "/employee/admin/settings/activate",
        data={
            "csrf_token": tokens(page.text, "/employee/admin/settings/activate")[0],
            "password": "correct-password",
            "expected_revision": "6",
            "deepseek_model": "new-model",
        },
    )

    assert response.status_code == 429
    assert response.headers["cache-control"] == "no-store"
    assert "认证服务繁忙" in response.text
    assert "password-sentinel-must-not-leak" not in response.text
