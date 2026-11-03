"""后台「小程序首页」：门店公开信息与首页横幅（小程序三标签 Spec F4），只允许管理员。"""

from typing import Annotated, BinaryIO, Protocol, cast

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import Employee
from homestay_bot.routes.admin_form_csrf import (
    MINIPROGRAM_HOME_CSRF_FAMILY,
    consume_form_csrf,
    issue_form_csrf,
)
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.routes.page_errors import raise_page_error
from homestay_bot.services.miniprogram_home_admin import (
    MAX_ADDRESS_CHARS,
    MAX_BANNERS,
    MAX_INTRO_CHARS,
    MAX_NAME_CHARS,
    MAX_PHONE_CHARS,
    HomeAdminView,
    StoreForm,
)
from homestay_bot.services.private_file_storage import StoredPrivateFile
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/admin/miniprogram")
PAGE_PATH = "/employee/admin/miniprogram"
# 门店信息只有一行，所有写操作共用固定实体编号；家族独立，与房源令牌互不通用。
CSRF_ENTITY = 1


class MiniProgramHomeAdminPort(Protocol):
    """定义后台「小程序首页」页面需要的服务接口。"""

    async def view(self, administrator: Employee) -> HomeAdminView:
        """返回页面数据。"""

    async def save_store(self, administrator: Employee, form: StoreForm) -> None:
        """保存门店信息。"""

    async def add_banner(
        self, administrator: Employee, stream: BinaryIO, content_type: str
    ) -> None:
        """上传一张横幅。"""

    async def delete_banner(self, administrator: Employee, banner_id: int) -> None:
        """删除一张横幅。"""

    async def reorder_banners(self, administrator: Employee, orders: dict[int, str]) -> None:
        """整体更新横幅排序号。"""

    async def banner_for(self, administrator: Employee, banner_id: int) -> StoredPrivateFile:
        """后台预览一张横幅。"""


def _service(request: Request) -> MiniProgramHomeAdminPort:
    """读取生命周期装配的服务。"""
    service = getattr(request.app.state, "miniprogram_home_admin", None)
    if service is None:
        raise HTTPException(status_code=503, detail="小程序首页管理尚未配置")
    return cast(MiniProgramHomeAdminPort, service)


async def _current_admin(request: Request) -> Employee:
    """持续复核员工会话并只允许管理员进入。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以管理小程序首页")
    return Employee(id=employee_id, wecom_userid="", name="", role=role, is_active=True)


def _raise(error: Exception) -> None:
    """把领域异常转换为稳定 HTTP 状态；校验错误原样提示给管理员。"""
    if isinstance(error, ValueError):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise_page_error(
        error,
        forbidden="没有权限管理小程序首页",
        not_found="横幅不存在",
        unknown="小程序首页操作未完成",
        log_subject="小程序首页管理操作失败",
    )


def _back(anchor: str = "") -> RedirectResponse:
    """写操作完成后回到页面。"""
    return RedirectResponse(PAGE_PATH + anchor, status_code=status.HTTP_303_SEE_OTHER)


async def _consume(request: Request, token: str) -> None:
    """校验并消费一次性表单令牌；失败返回 409。"""
    await consume_form_csrf(
        request, family=MINIPROGRAM_HOME_CSRF_FAMILY, entity_id=CSRF_ENTITY, token=token
    )


@router.get("", response_class=HTMLResponse)
async def miniprogram_home_page(request: Request) -> Response:
    """展示门店信息表单与横幅列表。"""
    administrator = await _current_admin(request)
    try:
        view = await _service(request).view(administrator)
    except Exception as error:
        _raise(error)
    return templates.TemplateResponse(
        request=request,
        name="admin/miniprogram_home.html",
        context={
            "home": view,
            "csrf_token": await issue_form_csrf(
                request, family=MINIPROGRAM_HOME_CSRF_FAMILY, entity_id=CSRF_ENTITY
            ),
            "limits": {
                "banners": MAX_BANNERS,
                "name": MAX_NAME_CHARS,
                "intro": MAX_INTRO_CHARS,
                "address": MAX_ADDRESS_CHARS,
                "phone": MAX_PHONE_CHARS,
            },
            "page_title": "小程序首页",
            "active_nav": "miniprogram",
        },
    )


@router.post("/store")
async def save_store(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    name: str = Form("", max_length=200),
    intro: str = Form("", max_length=2000),
    address: str = Form("", max_length=500),
    latitude: str = Form("", max_length=32),
    longitude: str = Form("", max_length=32),
    phone: str = Form("", max_length=64),
) -> RedirectResponse:
    """保存门店公开信息；长度上限由服务校验并给出可读提示。"""
    administrator = await _current_admin(request)
    await _consume(request, csrf_token)
    try:
        await _service(request).save_store(
            administrator,
            StoreForm(
                name=name,
                intro=intro,
                address=address,
                latitude=latitude,
                longitude=longitude,
                phone=phone,
            ),
        )
    except Exception as error:
        _raise(error)
    return _back()


@router.post("/banners")
async def upload_banner(
    request: Request,
    image: Annotated[UploadFile, File()],
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """上传一张首页横幅，排在最后。"""
    administrator = await _current_admin(request)
    await _consume(request, csrf_token)
    try:
        await _service(request).add_banner(
            administrator, image.file, image.content_type or "application/octet-stream"
        )
    except Exception as error:
        _raise(error)
    finally:
        await image.close()
    return _back("#banners")


@router.post("/banners/order")
async def reorder_banners(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """按 order_<编号> 字段整体更新横幅排序。"""
    administrator = await _current_admin(request)
    await _consume(request, csrf_token)
    form = await request.form()
    orders: dict[int, str] = {}
    for key, value in form.multi_items():
        if key.startswith("order_") and key[6:].isdigit() and isinstance(value, str):
            orders[int(key[6:])] = value
    if not orders or len(orders) > MAX_BANNERS:
        raise HTTPException(status_code=400, detail="排序提交无效")
    try:
        await _service(request).reorder_banners(administrator, orders)
    except Exception as error:
        _raise(error)
    return _back("#banners")


@router.post("/banners/{banner_id}/delete")
async def delete_banner(
    request: Request,
    banner_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """删除一张横幅，文件由清理任务删除。"""
    administrator = await _current_admin(request)
    await _consume(request, csrf_token)
    try:
        await _service(request).delete_banner(administrator, banner_id)
    except Exception as error:
        _raise(error)
    return _back("#banners")


@router.get("/banners/{banner_id}")
async def view_banner(request: Request, banner_id: int) -> Response:
    """后台预览横幅。"""
    administrator = await _current_admin(request)
    try:
        stored = await _service(request).banner_for(administrator, banner_id)
    except Exception as error:
        _raise(error)
    response = FileResponse(stored.path, media_type=stored.content_type, filename=None)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
