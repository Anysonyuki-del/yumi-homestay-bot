"""房间小程序介绍的后台维护（小程序住客自助 Spec F2）。

单独成模块而不并入 PropertyAdminService：这里只管会公开给任何人的介绍内容，
与地址、凭证等运营资料分开，权限仍复用 PropertyAdminService.require_admin。
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import (
    AuditLog,
    Employee,
    PropertyProfile,
    RoomShowcase,
    RoomShowcasePhoto,
)
from homestay_bot.services.property_admin_service import PropertyAdminService

MAX_PHOTOS_PER_ROOM = 9
MAX_FACILITIES = 30
MAX_FACILITY_CHARS = 20
MAX_INTRO_CHARS = 2000
MAX_SHORT_TEXT_CHARS = 64
MAX_SORT_ORDER = 9999


@dataclass(frozen=True)
class ShowcaseForm:
    """后台表单提交的原始文字，数字字段保持字符串，由服务统一校验。"""

    is_visible: bool
    display_name: str
    intro: str
    bed_type: str
    max_guests: str
    area_sqm: str
    facilities: str
    sort_order: str


@dataclass(frozen=True)
class ShowcasePhotoView:
    """后台照片列表的一项。"""

    id: int
    sort_order: int


@dataclass(frozen=True)
class ShowcaseView:
    """后台「小程序展示」页签需要的全部数据；尚未填写时各字段为空。"""

    property_title: str
    is_visible: bool
    display_name: str
    intro: str
    bed_type: str
    max_guests: int | None
    area_sqm: int | None
    facilities: tuple[str, ...]
    sort_order: int | None
    photos: tuple[ShowcasePhotoView, ...]


def _optional_int(raw: str, label: str, low: int, high: int) -> int | None:
    """把可空数字输入解析为整数；空白为不填，越界或非整数给出可读错误。"""
    text = raw.strip()
    if not text:
        return None
    if not text.isdigit() or not low <= int(text) <= high:
        raise ValueError(f"{label}请填写 {low} 到 {high} 之间的整数")
    return int(text)


def _clean_facilities(raw: str) -> list[str]:
    """一行一项：去掉空行和重复项，保留首次出现的顺序。"""
    items: list[str] = []
    for line in raw.splitlines():
        item = line.strip()
        if item and item not in items:
            items.append(item)
    if len(items) > MAX_FACILITIES:
        raise ValueError(f"设施清单最多 {MAX_FACILITIES} 项")
    if any(len(item) > MAX_FACILITY_CHARS for item in items):
        raise ValueError(f"每项设施不得超过 {MAX_FACILITY_CHARS} 个字")
    return items


class RoomShowcaseAdminService:
    """在调用方事务内维护房间介绍与照片记录；照片文件的存取由调用方负责。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库事务。"""
        self._session = session

    async def view(self, property_id: int, administrator: Employee) -> ShowcaseView:
        """返回后台展示页签数据，房间不存在时报 LookupError。"""
        PropertyAdminService.require_admin(administrator)
        property_profile = await self._session.get(PropertyProfile, property_id)
        if property_profile is None:
            raise LookupError("房源不存在")
        showcase = await self._session.get(RoomShowcase, property_id)
        photos = (
            await self._session.execute(
                select(RoomShowcasePhoto.id, RoomShowcasePhoto.sort_order)
                .where(RoomShowcasePhoto.property_id == property_id)
                .order_by(RoomShowcasePhoto.sort_order, RoomShowcasePhoto.id)
            )
        ).all()
        return ShowcaseView(
            property_title=property_profile.title,
            is_visible=bool(showcase and showcase.is_visible),
            display_name=(showcase.display_name or "") if showcase else "",
            intro=(showcase.intro or "") if showcase else "",
            bed_type=(showcase.bed_type or "") if showcase else "",
            max_guests=showcase.max_guests if showcase else None,
            area_sqm=showcase.area_sqm if showcase else None,
            facilities=tuple(showcase.facilities or ()) if showcase else (),
            sort_order=showcase.sort_order if showcase else None,
            photos=tuple(ShowcasePhotoView(id=row.id, sort_order=row.sort_order) for row in photos),
        )

    async def save(self, property_id: int, administrator: Employee, form: ShowcaseForm) -> None:
        """校验并保存文字资料；首次保存时新建介绍记录。"""
        PropertyAdminService.require_admin(administrator)
        display_name = form.display_name.strip()
        intro = form.intro.strip()
        bed_type = form.bed_type.strip()
        if len(display_name) > MAX_SHORT_TEXT_CHARS or len(bed_type) > MAX_SHORT_TEXT_CHARS:
            raise ValueError(f"展示名称和床型都不得超过 {MAX_SHORT_TEXT_CHARS} 个字")
        if len(intro) > MAX_INTRO_CHARS:
            raise ValueError(f"简介不得超过 {MAX_INTRO_CHARS} 个字")
        max_guests = _optional_int(form.max_guests, "可住人数", 1, 20)
        area_sqm = _optional_int(form.area_sqm, "面积", 1, 1000)
        sort_order = _optional_int(form.sort_order, "排序号", 0, MAX_SORT_ORDER)
        facilities = _clean_facilities(form.facilities)

        await self._lock_property(property_id)
        showcase = await self._session.get(RoomShowcase, property_id)
        if showcase is None:
            showcase = RoomShowcase(property_id=property_id)
            self._session.add(showcase)
        showcase.is_visible = form.is_visible
        showcase.display_name = display_name or None
        showcase.intro = intro or None
        showcase.bed_type = bed_type or None
        showcase.max_guests = max_guests
        showcase.area_sqm = area_sqm
        showcase.facilities = facilities
        showcase.sort_order = sort_order
        showcase.updated_by = administrator.id
        # 审计只记是否展示，不复制介绍正文。
        self._audit(
            administrator.id, "room_showcase_updated", property_id, {"is_visible": form.is_visible}
        )
        await self._session.flush()

    async def add_photo(self, property_id: int, administrator: Employee, file_id: str) -> None:
        """登记一张已落盘的照片，排在最后；锁定房间后计数，并发上传也不会超过上限。"""
        PropertyAdminService.require_admin(administrator)
        await self._lock_property(property_id)
        count, last_order = (
            await self._session.execute(
                select(func.count(), func.max(RoomShowcasePhoto.sort_order)).where(
                    RoomShowcasePhoto.property_id == property_id
                )
            )
        ).one()
        if count >= MAX_PHOTOS_PER_ROOM:
            raise ValueError(f"每间房最多 {MAX_PHOTOS_PER_ROOM} 张照片，请先删除再上传")
        self._session.add(
            RoomShowcasePhoto(
                property_id=property_id,
                file_id=file_id,
                sort_order=min((last_order if last_order is not None else -1) + 1, MAX_SORT_ORDER),
            )
        )
        self._audit(administrator.id, "room_showcase_photo_added", property_id, {})
        await self._session.flush()

    async def delete_photo(self, property_id: int, administrator: Employee, photo_id: int) -> str:
        """删除本房间的一张照片，返回文件编号供调用方登记清理；不属于本房间视为不存在。"""
        PropertyAdminService.require_admin(administrator)
        await self._lock_property(property_id)
        photo = await self._own_photo(property_id, photo_id)
        file_id = photo.file_id
        await self._session.delete(photo)
        self._audit(
            administrator.id, "room_showcase_photo_deleted", property_id, {"photo_id": photo_id}
        )
        await self._session.flush()
        return file_id

    async def reorder_photos(
        self, property_id: int, administrator: Employee, orders: dict[int, str]
    ) -> None:
        """按提交的排序号整体更新；任一编号不属于本房间或排序号无效则整批拒绝。"""
        PropertyAdminService.require_admin(administrator)
        parsed: dict[int, int] = {}
        for photo_id, raw in orders.items():
            value = _optional_int(raw, "照片排序号", 0, MAX_SORT_ORDER)
            if value is None:
                raise ValueError("照片排序号不能为空")
            parsed[photo_id] = value
        await self._lock_property(property_id)
        for photo_id, value in parsed.items():
            (await self._own_photo(property_id, photo_id)).sort_order = value
        self._audit(administrator.id, "room_showcase_photos_reordered", property_id, {})
        await self._session.flush()

    async def photo_file_id(self, property_id: int, administrator: Employee, photo_id: int) -> str:
        """后台预览照片：管理员可看未展示房间的照片。"""
        PropertyAdminService.require_admin(administrator)
        return (await self._own_photo(property_id, photo_id)).file_id

    async def _lock_property(self, property_id: int) -> None:
        """锁定房间行，串行化同一房间的介绍和照片写入。"""
        locked = await self._session.scalar(
            select(PropertyProfile.id).where(PropertyProfile.id == property_id).with_for_update()
        )
        if locked is None:
            raise LookupError("房源不存在")

    async def _own_photo(self, property_id: int, photo_id: int) -> RoomShowcasePhoto:
        """读取属于该房间的照片；编号属于别的房间时与不存在同样处理。"""
        photo = await self._session.scalar(
            select(RoomShowcasePhoto).where(
                RoomShowcasePhoto.id == photo_id,
                RoomShowcasePhoto.property_id == property_id,
            )
        )
        if photo is None:
            raise LookupError("照片不存在")
        return photo

    def _audit(
        self, employee_id: int, action: str, property_id: int, details: dict[str, object]
    ) -> None:
        """审计只记房间编号与动作，不含介绍正文或文件编号。"""
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="property_profile",
                target_id=str(property_id),
                details={"property_id": property_id, **details},
            )
        )
