import logging
import secrets
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import Employee
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.services.private_file_storage import StoredPrivateFile
from homestay_bot.services.property_admin_service import PropertyFields
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/properties")
logger = logging.getLogger(__name__)


class PropertyAdminServicePort(Protocol):
    """定义房源管理页面需要的最小服务接口。"""

    async def list_all(self, employee: Employee) -> list[Any]:
        """返回管理员可见房源。"""

    async def detail_for(
        self,
        property_id: int,
        employee: Employee,
    ) -> dict[str, object]:
        """返回不含凭证明文的房源详情。"""

    async def update_profile(
        self,
        property_id: int,
        employee: Employee,
        fields: PropertyFields,
    ) -> object:
        """更新房源运营资料。"""

    async def replace_credentials(
        self,
        property_id: int,
        employee: Employee,
        password: str,
        guide: str,
        stream: Any,
        content_type: str,
    ) -> object:
        """保存新版加密凭证和私有二维码。"""

    async def qr_for(
        self,
        property_id: int,
        employee: Employee,
    ) -> StoredPrivateFile:
        """授权后返回当前私有二维码。"""


def _get_service(request: Request) -> PropertyAdminServicePort:
    """从应用状态读取房源管理服务。"""
    service = getattr(request.app.state, "property_admin_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="房源管理服务尚未配置")
    return cast(PropertyAdminServicePort, service)


async def _current_admin(request: Request) -> Employee:
    """持续复核员工会话并只允许管理员进入。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以管理房源")
    return Employee(
        id=employee_id,
        wecom_userid="",
        name="",
        role=role,
        is_active=True,
    )


def _issue_csrf(request: Request, property_id: int) -> str:
    """为单个房源写操作签发一次性 CSRF 令牌。"""
    token = secrets.token_urlsafe(24)
    tokens = dict(request.session.get("property_csrf", {}))
    tokens[str(property_id)] = token
    request.session["property_csrf"] = tokens
    return token


def _consume_csrf(request: Request, property_id: int, token: str) -> None:
    """校验并立即消耗房源管理一次性令牌。"""
    tokens = dict(request.session.get("property_csrf", {}))
    expected = tokens.pop(str(property_id), None)
    request.session["property_csrf"] = tokens
    if not isinstance(expected, str) or not secrets.compare_digest(
        expected,
        token,
    ):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


def _raise_page_error(error: Exception) -> None:
    """把房源服务领域异常转换为稳定 HTTP 状态。"""
    if isinstance(error, PermissionError):
        raise HTTPException(
            status_code=403,
            detail="没有权限执行房源操作",
        ) from error
    if isinstance(error, LookupError):
        raise HTTPException(
            status_code=404,
            detail="房源不存在或凭证不可用",
        ) from error
    # 未知异常只记录类型和内部追踪号，页面不得回显异常原文。
    trace_id = secrets.token_hex(8)
    logger.error(
        "房源管理操作失败：error_type=%s trace_id=%s",
        type(error).__name__,
        trace_id,
    )
    raise HTTPException(
        status_code=409,
        detail="房源管理操作未完成",
    ) from error


@router.get("", response_class=HTMLResponse)
async def property_index(request: Request) -> Response:
    """展示管理员可配置的百居易房源。"""
    administrator = await _current_admin(request)
    try:
        properties = await _get_service(request).list_all(administrator)
    except Exception as error:
        _raise_page_error(error)
    return templates.TemplateResponse(
        request=request,
        name="properties/index.html",
        context={"properties": properties},
    )


@router.get("/{property_id}", response_class=HTMLResponse)
async def property_detail(request: Request, property_id: int) -> Response:
    """展示公开配置和凭证版本，绝不回显密码或指南。"""
    administrator = await _current_admin(request)
    try:
        detail = await _get_service(request).detail_for(
            property_id,
            administrator,
        )
    except Exception as error:
        _raise_page_error(error)
    return templates.TemplateResponse(
        request=request,
        name="properties/detail.html",
        context={
            **detail,
            "csrf_token": _issue_csrf(request, property_id),
        },
    )


@router.post("/{property_id}/profile")
async def update_property_profile(
    request: Request,
    property_id: int,
    title: str = Form(min_length=1, max_length=128),
    room_number: str = Form("", max_length=64),
    room_type: str = Form("", max_length=128),
    district: str = Form("", max_length=64),
    address_hint: str = Form("", max_length=1000),
    parking_instructions: str = Form("", max_length=2000),
    is_active: bool = Form(False),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """校验一次性令牌后更新房源公开运营资料。"""
    administrator = await _current_admin(request)
    _consume_csrf(request, property_id, csrf_token)
    try:
        await _get_service(request).update_profile(
            property_id,
            administrator,
            PropertyFields(
                title=title,
                room_number=room_number,
                room_type=room_type,
                district=district,
                address_hint=address_hint,
                parking_instructions=parking_instructions,
                is_active=is_active,
            ),
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/properties/{property_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{property_id}/credentials")
async def replace_property_credentials(
    request: Request,
    property_id: int,
    qr_image: Annotated[UploadFile, File()],
    password: str = Form(min_length=1, max_length=256),
    guide: str = Form(min_length=1, max_length=10_000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """保存用途隔离加密的密码、指南和私有二维码。"""
    administrator = await _current_admin(request)
    _consume_csrf(request, property_id, csrf_token)
    try:
        await _get_service(request).replace_credentials(
            property_id,
            administrator,
            password,
            guide,
            qr_image.file,
            qr_image.content_type or "application/octet-stream",
        )
    except Exception as error:
        _raise_page_error(error)
    finally:
        await qr_image.close()
    return RedirectResponse(
        f"/employee/properties/{property_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{property_id}/qr")
async def download_property_qr(
    request: Request,
    property_id: int,
) -> Response:
    """只向管理员返回当前房源的私有入住二维码。"""
    administrator = await _current_admin(request)
    try:
        stored = await _get_service(request).qr_for(
            property_id,
            administrator,
        )
    except Exception as error:
        _raise_page_error(error)
    response = FileResponse(
        stored.path,
        media_type=stored.content_type,
        filename=None,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
