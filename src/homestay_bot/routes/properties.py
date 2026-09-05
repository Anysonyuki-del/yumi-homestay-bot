import logging
from typing import Annotated, Any, Literal, Protocol, cast

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole, RoomOperationalStatus
from homestay_bot.domain.models import Employee
from homestay_bot.routes.admin_form_csrf import (
    PROPERTY_CSRF_FAMILY,
    consume_form_csrf,
    drop_legacy_session_key,
    issue_form_csrf,
)
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.routes.page_errors import raise_page_error
from homestay_bot.services.private_file_storage import StoredPrivateFile
from homestay_bot.services.property_admin_service import PropertyFields
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/properties")
logger = logging.getLogger(__name__)


def _raise_page_error(error: Exception) -> None:
    """把房源页面领域异常转换为稳定 HTTP 状态。"""
    raise_page_error(
        error,
        forbidden="没有权限执行房源操作",
        not_found="房源不存在或凭证不可用",
        unknown="房源管理操作未完成",
        log_subject="房源管理操作失败",
    )


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


async def _issue_csrf(request: Request, property_id: int) -> str:
    """为单个房源写操作签发服务端一次性令牌。"""
    drop_legacy_session_key(request, "property_csrf")
    return await issue_form_csrf(
        request,
        family=PROPERTY_CSRF_FAMILY,
        entity_id=property_id,
    )


async def _consume_csrf(request: Request, property_id: int, token: str) -> None:
    """校验并原子消费房源令牌；令牌绑定该房源，跨房源重放必然失败。"""
    await consume_form_csrf(
        request,
        family=PROPERTY_CSRF_FAMILY,
        entity_id=property_id,
        token=token,
    )



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
        context={
            "properties": properties,
            "page_title": "房源管理",
            "active_nav": "properties",
        },
    )


@router.get("/{property_id}", response_class=HTMLResponse)
async def property_detail(
    request: Request,
    property_id: int,
    tab: Literal["overview", "profile", "credentials"] = "overview",
) -> Response:
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
            "tab": tab,
            "room_statuses": list(RoomOperationalStatus),
            "csrf_token": await _issue_csrf(request, property_id),
            "page_title": getattr(
                detail["property"],
                "title",
                f"房源 #{property_id}",
            ),
            "active_nav": "properties",
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
    await _consume_csrf(request, property_id, csrf_token)
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
        f"/employee/properties/{property_id}?tab=profile",
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
    await _consume_csrf(request, property_id, csrf_token)
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
        f"/employee/properties/{property_id}?tab=credentials",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{property_id}/room-status")
async def set_room_operational_status(
    request: Request,
    property_id: int,
    room_status: Annotated[RoomOperationalStatus, Form()],
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """允许管理员直接设定房态，不要求清单与现场照片证据。"""
    administrator = await _current_admin(request)
    await _consume_csrf(request, property_id, csrf_token)
    service = getattr(request.app.state, "room_readiness_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="房态服务未就绪")
    try:
        await service.set_status_by_admin(property_id, administrator, room_status)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail="没有权限设定房态") from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail="房间不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
