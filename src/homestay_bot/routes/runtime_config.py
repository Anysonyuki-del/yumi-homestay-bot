"""提供仅唯一管理员可访问的加密运行配置设置与回滚页面。"""

import logging
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.routing import APIRoute

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.repositories.runtime_config import (
    RuntimeConfigConflictError,
    RuntimeConfigRollbackError,
)
from homestay_bot.routes.employee_auth import (
    AdminCsrfServicePort,
    require_employee_session,
)
from homestay_bot.services.admin_auth_service import Argon2CapacityError, AuthenticationError
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError
from homestay_bot.services.runtime_config_service import (
    ActivationResult,
    RuntimeConfigPage,
    RuntimeConfigTestError,
    RuntimeConfigUnavailableError,
    RuntimeConfigVersionView,
    UpdateRuntimeConfig,
)
from homestay_bot.web import templates

logger = logging.getLogger(__name__)
ACTIVATE_CSRF_PURPOSE = "runtime-config-activate"
ROLLBACK_CSRF_PURPOSE = "runtime-config-rollback"


def _add_no_store_headers(response: Response) -> Response:
    """为设置域全部响应统一禁止浏览器和代理缓存。"""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


class RuntimeConfigSafeRoute(APIRoute):
    """把框架级校验和 HTTP 错误转换为不回显输入的安全响应。"""

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        """包装 FastAPI 生成的处理器，并保留跳转等受控响应头。"""
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            """捕获进入端点前的异常，确保设置路径始终脱敏且 no-store。"""
            try:
                response = await original_handler(request)
            except RequestValidationError:
                response = HTMLResponse(
                    "设置表单格式无效，请刷新页面后重试。",
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            except HTTPException as error:
                response = HTMLResponse(
                    "设置请求未完成，请刷新页面后重试。",
                    status_code=error.status_code,
                    headers=error.headers,
                )
            return _add_no_store_headers(response)

        return safe_handler


router = APIRouter(
    prefix="/employee/admin/settings",
    route_class=RuntimeConfigSafeRoute,
)


class RuntimeConfigServicePort(Protocol):
    """定义设置路由所需的脱敏读取和敏感写入接口。"""

    async def page_data(self) -> RuntimeConfigPage:
        """返回当前安全页面投影和 CAS 指针。"""

    async def list_version_views(
        self,
        *,
        limit: int = 20,
    ) -> list[RuntimeConfigVersionView]:
        """返回不含密文的有界版本历史。"""

    async def create_and_test(
        self,
        command: UpdateRuntimeConfig,
        *,
        actor_id: int,
        admin_id: int,
        password: str,
        expected_session_version: int,
        expected_revision: int,
    ) -> ActivationResult:
        """创建、测试并尝试激活候选。"""

    async def rollback(
        self,
        *,
        actor_id: int,
        admin_id: int,
        password: str,
        expected_session_version: int,
        expected_revision: int,
        expected_previous_version_id: int,
    ) -> ActivationResult:
        """复核后回滚到页面绑定的上一版本。"""


def _service(request: Request) -> RuntimeConfigServicePort:
    """从应用生命周期读取运行配置服务。"""
    value = getattr(request.app.state, "runtime_config_service", None)
    if value is None:
        raise HTTPException(status_code=503, detail="运行配置服务尚未配置")
    return cast(RuntimeConfigServicePort, value)


def _csrf_service(request: Request) -> AdminCsrfServicePort:
    """读取服务端原子 nonce 服务，禁止退回 Cookie CSRF。"""
    value = getattr(request.app.state, "admin_csrf_service", None)
    if value is None:
        raise HTTPException(status_code=503, detail="认证表单安全服务尚未配置")
    return cast(AdminCsrfServicePort, value)


async def _admin_context(request: Request) -> tuple[int, int, int]:
    """持续复核管理员并读取当前凭证及会话版本。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以管理运行配置")
    admin_id = request.session.get("admin_id")
    session_version = request.session.get("admin_session_version")
    if not isinstance(admin_id, int) or not isinstance(session_version, int):
        raise HTTPException(status_code=401, detail="管理员会话无效")
    return employee_id, admin_id, session_version


async def _issue_nonce(request: Request, purpose: str, admin_id: int) -> str:
    """每次页面渲染签发独立 nonce，支持多个标签页分别提交。"""
    try:
        return await _csrf_service(request).issue(purpose, admin_id=admin_id)
    except AdminCsrfCapacityError as error:
        raise HTTPException(status_code=429, detail="设置表单请求过于频繁") from error


async def _consume_nonce(
    request: Request,
    token: str,
    purpose: str,
    admin_id: int,
) -> None:
    """按用途和管理员原子消费一次性 nonce，拒绝伪造与重放。"""
    if not await _csrf_service(request).consume(token, purpose, admin_id=admin_id):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


def _no_store(response: Response) -> Response:
    """禁止浏览器、代理和历史记录缓存设置页面或操作结果。"""
    return _add_no_store_headers(response)


async def _render_settings(
    request: Request,
    *,
    admin_id: int,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """读取安全投影并签发两个用途隔离的多标签页 nonce。"""
    page = await _service(request).page_data()
    versions = await _service(request).list_version_views(limit=8)
    response = templates.TemplateResponse(
        request=request,
        name="admin/settings.html",
        context={
            "page_title": "接口与模型设置",
            "active_nav": "settings",
            "page": page,
            "versions": versions,
            "activate_csrf_token": await _issue_nonce(
                request,
                ACTIVATE_CSRF_PURPOSE,
                admin_id,
            ),
            "rollback_csrf_token": await _issue_nonce(
                request,
                ROLLBACK_CSRF_PURPOSE,
                admin_id,
            ),
            "error": error,
        },
        status_code=status_code,
    )
    return _no_store(response)


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request) -> Response:
    """展示当前配置的掩码视图、编辑表单与上一版本回滚入口。"""
    _, admin_id, _ = await _admin_context(request)
    return await _render_settings(request, admin_id=admin_id)


@router.get("/versions", response_class=HTMLResponse)
async def config_versions_page(request: Request) -> Response:
    """展示有界且不含密文的配置版本历史。"""
    _, admin_id, _ = await _admin_context(request)
    page = await _service(request).page_data()
    response = templates.TemplateResponse(
        request=request,
        name="admin/config_versions.html",
        context={
            "page_title": "配置版本记录",
            "active_nav": "settings",
            "page": page,
            "versions": await _service(request).list_version_views(limit=50),
            "rollback_csrf_token": await _issue_nonce(
                request,
                ROLLBACK_CSRF_PURPOSE,
                admin_id,
            ),
        },
    )
    return _no_store(response)


@router.post("/activate")
async def activate_settings(
    request: Request,
    csrf_token: Annotated[str, Form(min_length=1, max_length=256)],
    password: Annotated[str, Form()],
    expected_revision: Annotated[int, Form(ge=0)],
    deepseek_api_key: Annotated[str | None, Form()] = None,
    deepseek_base_url: Annotated[str | None, Form(max_length=2048)] = None,
    deepseek_model: Annotated[str | None, Form(max_length=256)] = None,
    hostex_access_token: Annotated[str | None, Form()] = None,
    hostex_webhook_secret_token: Annotated[str | None, Form()] = None,
    hostex_reconcile_interval_seconds: Annotated[float | None, Form(ge=60, le=86400)] = None,
    wecom_corp_id: Annotated[str | None, Form()] = None,
    wecom_kf_secret: Annotated[str | None, Form()] = None,
    wecom_callback_token: Annotated[str | None, Form()] = None,
    wecom_encoding_aes_key: Annotated[str | None, Form()] = None,
    wecom_agent_id: Annotated[int | None, Form(gt=0)] = None,
    wecom_agent_secret: Annotated[str | None, Form()] = None,
    wecom_contact_secret: Annotated[str | None, Form()] = None,
    wecom_duty_userids: Annotated[str | None, Form()] = None,
    wecom_poll_interval_seconds: Annotated[float | None, Form(ge=5, le=300)] = None,
    clear_wecom_contact_secret: Annotated[bool, Form()] = False,
) -> Response:
    """消费激活 nonce，绑定会话版本与页面 revision 后测试并保存候选。"""
    employee_id, admin_id, session_version = await _admin_context(request)
    await _consume_nonce(request, csrf_token, ACTIVATE_CSRF_PURPOSE, admin_id)
    sensitive_limits = (
        (password, 128),
        (deepseek_api_key, 4096),
        (hostex_access_token, 4096),
        (hostex_webhook_secret_token, 4096),
        (wecom_corp_id, 256),
        (wecom_kf_secret, 4096),
        (wecom_callback_token, 4096),
        (wecom_encoding_aes_key, 43),
        (wecom_agent_secret, 4096),
        (wecom_contact_secret, 4096),
        (wecom_duty_userids, 4096),
    )
    if not password or any(
        value is not None and len(value) > limit for value, limit in sensitive_limits
    ):
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="输入内容过长或管理员密码为空，请检查后重试。",
        )
    command = UpdateRuntimeConfig(
        deepseek_api_key=deepseek_api_key,
        deepseek_base_url=deepseek_base_url,
        deepseek_model=deepseek_model,
        hostex_access_token=hostex_access_token,
        hostex_webhook_secret_token=hostex_webhook_secret_token,
        hostex_reconcile_interval_seconds=hostex_reconcile_interval_seconds,
        wecom_corp_id=wecom_corp_id,
        wecom_kf_secret=wecom_kf_secret,
        wecom_callback_token=wecom_callback_token,
        wecom_encoding_aes_key=wecom_encoding_aes_key,
        wecom_agent_id=wecom_agent_id,
        wecom_agent_secret=wecom_agent_secret,
        wecom_contact_secret=wecom_contact_secret,
        wecom_duty_userids=wecom_duty_userids,
        wecom_poll_interval_seconds=wecom_poll_interval_seconds,
        clear_wecom_contact_secret=clear_wecom_contact_secret,
    )
    try:
        await _service(request).create_and_test(
            command,
            actor_id=employee_id,
            admin_id=admin_id,
            password=password,
            expected_session_version=session_version,
            expected_revision=expected_revision,
        )
    except AuthenticationError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="当前密码验证失败，配置未保存。",
        )
    except Argon2CapacityError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="认证服务繁忙，请稍后重试。",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    except RuntimeConfigUnavailableError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="配置主密钥未就绪，当前为只读模式，配置未保存。",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except RuntimeConfigTestError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="候选配置测试未通过，旧版本继续使用。",
        )
    except (RuntimeConfigConflictError, ValueError):
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="页面配置已变化或输入无效，请核对后重试。",
        )
    except Exception as error:
        logger.error("运行配置激活失败：error_type=%s", type(error).__name__)
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="配置操作暂未完成，请稍后重试。",
        )
    response = RedirectResponse(
        "/employee/admin/settings",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return _no_store(response)


@router.post("/rollback")
async def rollback_settings(
    request: Request,
    csrf_token: Annotated[str, Form(min_length=1, max_length=256)],
    password: Annotated[str, Form()],
    expected_revision: Annotated[int, Form(ge=0)],
    expected_previous_version_id: Annotated[int, Form(gt=0)],
) -> Response:
    """消费独立回滚 nonce，并同时绑定两个页面 CAS 值执行回退。"""
    employee_id, admin_id, session_version = await _admin_context(request)
    await _consume_nonce(request, csrf_token, ROLLBACK_CSRF_PURPOSE, admin_id)
    if not password or len(password) > 128:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="输入内容过长或管理员密码为空，请检查后重试。",
        )
    try:
        await _service(request).rollback(
            actor_id=employee_id,
            admin_id=admin_id,
            password=password,
            expected_session_version=session_version,
            expected_revision=expected_revision,
            expected_previous_version_id=expected_previous_version_id,
        )
    except AuthenticationError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="当前密码验证失败，未执行回滚。",
        )
    except Argon2CapacityError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="认证服务繁忙，请稍后重试。",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    except RuntimeConfigUnavailableError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="配置主密钥未就绪，当前为只读模式，未执行回滚。",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except RuntimeConfigTestError:
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="上一版本连接测试未通过，未执行回滚。",
        )
    except (RuntimeConfigConflictError, RuntimeConfigRollbackError, LookupError):
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="回滚目标已变化，请刷新页面后重试。",
        )
    except Exception as error:
        logger.error("运行配置回滚失败：error_type=%s", type(error).__name__)
        return await _render_settings(
            request,
            admin_id=admin_id,
            error="回滚操作暂未完成，请稍后重试。",
        )
    response = RedirectResponse(
        "/employee/admin/settings",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    return _no_store(response)
