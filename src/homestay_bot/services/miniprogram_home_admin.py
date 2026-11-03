"""后台维护小程序首页：门店公开信息与首页横幅（小程序三标签 Spec F4）。

这里写入的内容会公开给任何打开小程序的人；权限复用 PropertyAdminService.require_admin，
写操作都锁门店信息那一行，串行化并发保存与横幅上传。
"""

import math
import re
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.domain.models import (
    AuditLog,
    Employee,
    MiniProgramBanner,
    MiniProgramStoreProfile,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.services.image_metadata import save_public_image
from homestay_bot.services.private_file_storage import PrivateFileStorage, StoredPrivateFile
from homestay_bot.services.property_admin_service import PropertyAdminService
from homestay_bot.services.task_page_service import ATTACHMENT_CLEANUP_JOB_TYPE

MAX_BANNERS = 6
MAX_NAME_CHARS = 32
MAX_INTRO_CHARS = 300
MAX_ADDRESS_CHARS = 120
MAX_PHONE_CHARS = 20
MAX_SORT_ORDER = 9999
STORE_PROFILE_ID = 1
# 电话只收数字、空格、加号、短横线和括号，小程序拨号前会去掉非数字字符。
_PHONE_PATTERN = re.compile(r"[0-9+\-() ]+")


@dataclass(frozen=True)
class StoreForm:
    """后台表单提交的原始文字；坐标保持字符串，由服务统一校验。"""

    name: str
    intro: str
    address: str
    latitude: str
    longitude: str
    phone: str


@dataclass(frozen=True)
class BannerView:
    """后台横幅列表的一项。"""

    id: int
    sort_order: int


@dataclass(frozen=True)
class HomeAdminView:
    """后台「小程序首页」页面需要的全部数据；未填写的字段为空串。"""

    name: str
    intro: str
    address: str
    latitude: float | None
    longitude: float | None
    phone: str
    banners: tuple[BannerView, ...]


def _coordinate(raw: str, label: str, limit: float) -> float | None:
    """把可空坐标输入解析为浮点数；空白为不填，非数字或越界给出可读错误。"""
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{label}请填写数字") from None
    if not math.isfinite(value) or not -limit <= value <= limit:
        raise ValueError(f"{label}应在 -{limit:g} 到 {limit:g} 之间")
    return value


def _validated(form: StoreForm) -> dict[str, object]:
    """校验门店信息并返回要写入的字段；空白统一存为 None。"""
    name = form.name.strip()
    intro = form.intro.strip()
    address = form.address.strip()
    phone = form.phone.strip()
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"民宿名不得超过 {MAX_NAME_CHARS} 个字")
    if len(intro) > MAX_INTRO_CHARS:
        raise ValueError(f"简介不得超过 {MAX_INTRO_CHARS} 个字")
    if len(address) > MAX_ADDRESS_CHARS:
        raise ValueError(f"地址不得超过 {MAX_ADDRESS_CHARS} 个字")
    if phone and (len(phone) > MAX_PHONE_CHARS or not _PHONE_PATTERN.fullmatch(phone)):
        raise ValueError(f"电话只能含数字、空格、+、- 和括号，且不超过 {MAX_PHONE_CHARS} 个字符")
    latitude = _coordinate(form.latitude, "纬度", 90)
    longitude = _coordinate(form.longitude, "经度", 180)
    if (latitude is None) != (longitude is None):
        raise ValueError("纬度和经度要么都填，要么都不填")
    return {
        "name": name or None,
        "intro": intro or None,
        "address": address or None,
        "latitude": latitude,
        "longitude": longitude,
        "phone": phone or None,
    }


class MiniProgramHomeAdminService:
    """在调用方事务内维护门店信息与横幅记录；横幅文件的存取由调用方负责。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库事务。"""
        self._session = session

    async def view(self, administrator: Employee) -> HomeAdminView:
        """返回后台页面数据。"""
        PropertyAdminService.require_admin(administrator)
        profile = await self._session.get(MiniProgramStoreProfile, STORE_PROFILE_ID)
        rows = (
            await self._session.execute(
                select(MiniProgramBanner.id, MiniProgramBanner.sort_order).order_by(
                    MiniProgramBanner.sort_order, MiniProgramBanner.id
                )
            )
        ).all()
        return HomeAdminView(
            name=(profile.name or "") if profile else "",
            intro=(profile.intro or "") if profile else "",
            address=(profile.address or "") if profile else "",
            latitude=profile.latitude if profile else None,
            longitude=profile.longitude if profile else None,
            phone=(profile.phone or "") if profile else "",
            banners=tuple(BannerView(id=row.id, sort_order=row.sort_order) for row in rows),
        )

    async def save_store(self, administrator: Employee, form: StoreForm) -> None:
        """校验并保存门店信息。"""
        PropertyAdminService.require_admin(administrator)
        fields = _validated(form)
        profile = await self._lock()
        for key, value in fields.items():
            setattr(profile, key, value)
        profile.updated_by = administrator.id
        # 审计只记动作，不复制公开文字，也不记电话。
        self._audit(administrator.id, "miniprogram_store_updated", {})
        await self._session.flush()

    async def add_banner(self, administrator: Employee, file_id: str) -> None:
        """登记一张已落盘的横幅，排在最后；锁定后计数，并发上传也不会超过上限。"""
        PropertyAdminService.require_admin(administrator)
        await self._lock()
        count, last_order = (
            await self._session.execute(
                select(func.count(), func.max(MiniProgramBanner.sort_order))
            )
        ).one()
        if count >= MAX_BANNERS:
            raise ValueError(f"首页横幅最多 {MAX_BANNERS} 张，请先删除再上传")
        self._session.add(
            MiniProgramBanner(
                file_id=file_id,
                sort_order=min((last_order if last_order is not None else -1) + 1, MAX_SORT_ORDER),
            )
        )
        self._audit(administrator.id, "miniprogram_banner_added", {})
        await self._session.flush()

    async def delete_banner(self, administrator: Employee, banner_id: int) -> str:
        """删除一张横幅，返回文件编号供调用方登记清理。"""
        PropertyAdminService.require_admin(administrator)
        await self._lock()
        banner = await self._banner(banner_id)
        file_id = banner.file_id
        await self._session.delete(banner)
        self._audit(administrator.id, "miniprogram_banner_deleted", {"banner_id": banner_id})
        await self._session.flush()
        return file_id

    async def reorder_banners(self, administrator: Employee, orders: dict[int, str]) -> None:
        """按提交的排序号整体更新；任一编号不存在或排序号无效则整批拒绝。"""
        PropertyAdminService.require_admin(administrator)
        parsed: dict[int, int] = {}
        for banner_id, raw in orders.items():
            text = raw.strip()
            if not text.isdigit() or int(text) > MAX_SORT_ORDER:
                raise ValueError(f"横幅排序号请填写 0 到 {MAX_SORT_ORDER} 之间的整数")
            parsed[banner_id] = int(text)
        await self._lock()
        for banner_id, value in parsed.items():
            (await self._banner(banner_id)).sort_order = value
        self._audit(administrator.id, "miniprogram_banners_reordered", {})
        await self._session.flush()

    async def banner_file_id(self, administrator: Employee, banner_id: int) -> str:
        """后台预览横幅。"""
        PropertyAdminService.require_admin(administrator)
        return (await self._banner(banner_id)).file_id

    async def _lock(self) -> MiniProgramStoreProfile:
        """锁定门店信息行；该行由迁移预置，缺失说明迁移未执行。"""
        profile = await self._session.scalar(
            select(MiniProgramStoreProfile)
            .where(MiniProgramStoreProfile.id == STORE_PROFILE_ID)
            .with_for_update()
        )
        if profile is None:
            raise LookupError("门店信息未初始化")
        return profile

    async def _banner(self, banner_id: int) -> MiniProgramBanner:
        """读取横幅；不存在时报 LookupError。"""
        banner = await self._session.get(MiniProgramBanner, banner_id)
        if banner is None:
            raise LookupError("横幅不存在")
        return banner

    def _audit(self, employee_id: int, action: str, details: dict[str, object]) -> None:
        """审计只记动作与横幅编号，不含公开文字或文件编号。"""
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="miniprogram_home",
                target_id=str(STORE_PROFILE_ID),
                details=details,
            )
        )


class SessionMiniProgramHomeAdmin:
    """为每次后台请求创建独立事务，并负责横幅文件的落盘、回滚删除与清理登记。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        storage: PrivateFileStorage,
        upload_size_limit: int,
    ) -> None:
        """保存会话工厂、私有文件存储和上传大小上限。"""
        self._factory = factory
        self._storage = storage
        self._upload_size_limit = upload_size_limit

    async def view(self, administrator: Employee) -> HomeAdminView:
        """返回后台页面数据。"""
        async with self._factory() as session:
            return await MiniProgramHomeAdminService(session).view(administrator)

    async def save_store(self, administrator: Employee, form: StoreForm) -> None:
        """保存门店信息。"""
        async with self._factory() as session:
            await MiniProgramHomeAdminService(session).save_store(administrator, form)
            await session.commit()

    async def add_banner(
        self, administrator: Employee, stream: BinaryIO, content_type: str
    ) -> None:
        """去除元数据后落盘并登记横幅；登记失败时删掉刚写的文件，不留孤儿。"""
        PropertyAdminService.require_admin(administrator)
        stored = await save_public_image(
            self._storage, stream, content_type, self._upload_size_limit
        )
        try:
            async with self._factory() as session:
                await MiniProgramHomeAdminService(session).add_banner(administrator, stored.file_id)
                await session.commit()
        except BaseException:
            self._storage.delete(stored.file_id)
            raise

    async def delete_banner(self, administrator: Employee, banner_id: int) -> None:
        """删除横幅记录，并在同一事务登记文件清理任务。"""
        async with self._factory() as session:
            file_id = await MiniProgramHomeAdminService(session).delete_banner(
                administrator, banner_id
            )
            await SQLAlchemyJobRepository(session).enqueue(
                ATTACHMENT_CLEANUP_JOB_TYPE,
                {"file_ids": [file_id]},
                dedupe_key=f"miniprogram-banner-cleanup:{file_id}",
            )
            await session.commit()

    async def reorder_banners(self, administrator: Employee, orders: dict[int, str]) -> None:
        """整体更新横幅排序号。"""
        async with self._factory() as session:
            await MiniProgramHomeAdminService(session).reorder_banners(administrator, orders)
            await session.commit()

    async def banner_for(self, administrator: Employee, banner_id: int) -> StoredPrivateFile:
        """后台预览一张横幅。"""
        async with self._factory() as session:
            file_id = await MiniProgramHomeAdminService(session).banner_file_id(
                administrator, banner_id
            )
        return self._storage.open_for_read(file_id)
