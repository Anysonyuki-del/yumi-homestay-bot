"""小程序住客自助的公开只读接口（住客自助 Spec F1、F3，三标签 Spec F4），不需要登录。"""

import logging
from typing import Protocol, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response

from homestay_bot.services.miniprogram_content import (
    GuideSection,
    HomeContent,
    RoomCard,
    RoomDetail,
)
from homestay_bot.services.private_file_storage import StoredPrivateFile

router = APIRouter(prefix="/api/miniprogram")
logger = logging.getLogger(__name__)


class MiniProgramContentPort(Protocol):
    """定义路由所需的公开内容服务接口。"""

    async def guide(self) -> list[GuideSection]:
        """返回入住指南分组。"""

    async def rooms(self) -> list[RoomCard]:
        """返回可见房间列表。"""

    async def room(self, property_id: int) -> RoomDetail | None:
        """返回可见房间详情。"""

    async def photo(self, property_id: int, photo_id: int) -> StoredPrivateFile | None:
        """返回可见房间的一张照片。"""

    async def home(self) -> HomeContent:
        """返回首页门店信息与横幅。"""

    async def banner(self, banner_id: int) -> StoredPrivateFile | None:
        """返回一张首页横幅。"""


# 照片文件不可变：替换照片会生成新编号，所以允许客户端和中间层缓存一天（Spec D1）。
PHOTO_CACHE_CONTROL = "public, max-age=86400"


def _unavailable() -> JSONResponse:
    """服务未装配或本地故障时返回固定文案，不透传异常内容。"""
    return JSONResponse(
        {"error": "unavailable", "message": "内容暂时无法加载，请稍后重试"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _not_found() -> JSONResponse:
    """不可见与不存在统一返回 404，不泄露房间是否存在。"""
    return JSONResponse(
        {"error": "not_found", "message": "内容不存在或已下架"},
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _photo_url(property_id: int, photo_id: int) -> str:
    """照片的站内路径；小程序端拼上后端地址使用。"""
    return f"/api/miniprogram/rooms/{property_id}/photos/{photo_id}"


def _card_json(card: RoomCard) -> dict[str, object]:
    """房间列表项只输出 Spec 列出的字段。"""
    return {
        "id": card.id,
        "name": card.name,
        "bed_type": card.bed_type,
        "max_guests": card.max_guests,
        "area_sqm": card.area_sqm,
        "cover_url": (
            _photo_url(card.id, card.cover_photo_id) if card.cover_photo_id is not None else None
        ),
        "summary": card.summary,
    }


def _service(request: Request) -> MiniProgramContentPort | None:
    """读取生命周期装配的公开内容服务。"""
    return cast(
        MiniProgramContentPort | None,
        getattr(request.app.state, "miniprogram_content_service", None),
    )


@router.get("/guide")
async def read_guide(request: Request) -> JSONResponse:
    """返回入住指南；只输出分类、中文问题和答案。"""
    service = _service(request)
    if service is None:
        return _unavailable()
    try:
        sections = await service.guide()
    except Exception as error:
        # 只记异常类型：数据库异常正文可能带 SQL 参数。
        logger.warning("小程序入住指南读取失败：error=%s", type(error).__name__)
        return _unavailable()
    return JSONResponse(
        {
            "sections": [
                {
                    "category": section.category,
                    "items": [
                        {"question": item.question, "answer": item.answer} for item in section.items
                    ],
                }
                for section in sections
            ]
        }
    )


@router.get("/rooms")
async def read_rooms(request: Request) -> JSONResponse:
    """返回可见房间列表。"""
    service = _service(request)
    if service is None:
        return _unavailable()
    try:
        rooms = await service.rooms()
    except Exception as error:
        logger.warning("小程序房间列表读取失败：error=%s", type(error).__name__)
        return _unavailable()
    return JSONResponse({"rooms": [_card_json(card) for card in rooms]})


@router.get("/rooms/{property_id}")
async def read_room(request: Request, property_id: int) -> JSONResponse:
    """返回可见房间详情；不含地址、停车说明、房号等运营字段。"""
    service = _service(request)
    if service is None:
        return _unavailable()
    try:
        detail = await service.room(property_id)
    except Exception as error:
        logger.warning("小程序房间详情读取失败：error=%s", type(error).__name__)
        return _unavailable()
    if detail is None:
        return _not_found()
    return JSONResponse(
        {
            **_card_json(detail.card),
            "intro": detail.intro,
            "facilities": list(detail.facilities),
            "photos": [_photo_url(property_id, photo_id) for photo_id in detail.photo_ids],
        }
    )


@router.get("/rooms/{property_id}/photos/{photo_id}")
async def read_room_photo(request: Request, property_id: int, photo_id: int) -> Response:
    """返回可见房间的照片文件；路径只含数字编号，文件位置由数据库关系决定。"""
    service = _service(request)
    if service is None:
        return _public_file(None, _unavailable())
    try:
        stored = await service.photo(property_id, photo_id)
    except Exception as error:
        logger.warning("小程序房间照片读取失败：error=%s", type(error).__name__)
        return _public_file(None, _unavailable())
    return _public_file(stored, None)


@router.get("/home")
async def read_home(request: Request) -> JSONResponse:
    """首页：门店公开信息（未配置为 null）与横幅地址列表。"""
    service = _service(request)
    if service is None:
        return _unavailable()
    try:
        home = await service.home()
    except Exception as error:
        logger.warning("小程序首页读取失败：error=%s", type(error).__name__)
        return _unavailable()
    store = home.store
    return JSONResponse(
        {
            "store": (
                None
                if store is None
                else {
                    "name": store.name,
                    "intro": store.intro,
                    "address": store.address,
                    "latitude": store.latitude,
                    "longitude": store.longitude,
                    "phone": store.phone,
                }
            ),
            "banners": [f"/api/miniprogram/banners/{banner_id}" for banner_id in home.banner_ids],
        }
    )


@router.get("/banners/{banner_id}")
async def read_banner(request: Request, banner_id: int) -> Response:
    """返回一张首页横幅文件；文件位置由横幅记录决定。"""
    service = _service(request)
    if service is None:
        return _public_file(None, _unavailable())
    try:
        stored = await service.banner(banner_id)
    except Exception as error:
        logger.warning("小程序首页横幅读取失败：error=%s", type(error).__name__)
        return _public_file(None, _unavailable())
    return _public_file(stored, None)


def _public_file(stored: StoredPrivateFile | None, failure: JSONResponse | None) -> Response:
    """公开图片响应：成功可缓存一天；失败显式 no-store。

    图片路径不经过统一的 no-store 中间件；失败结果不能被缓存，否则内容重新上架后
    客户端仍会拿到旧的 404。
    """
    if failure is not None or stored is None:
        rejected = failure or _not_found()
        rejected.headers["Cache-Control"] = "no-store"
        return rejected
    response = FileResponse(stored.path, media_type=stored.content_type, filename=None)
    response.headers["Cache-Control"] = PHOTO_CACHE_CONTROL
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
