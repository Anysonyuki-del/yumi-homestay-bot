import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    EmployeeRole,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    BusinessTask,
    Employee,
    PropertyProfile,
    RoomCredential,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@dataclass(frozen=True)
class PropertyFields:
    """描述管理员可维护的房源公开运营资料。"""

    title: str
    room_type: str
    district: str
    address_hint: str
    parking_instructions: str
    is_active: bool
    room_number: str = ""


@dataclass(frozen=True, slots=True)
class PropertyOverview:
    """房源列表与详情概览共用的安全运营投影。"""

    id: int
    title: str
    room_number: str | None
    room_type: str | None
    district: str | None
    address_hint: str | None
    parking_instructions: str | None
    is_active: bool
    operational_status: RoomOperationalStatus
    today_stay_labels: tuple[str, ...]
    open_task_count: int
    credential_version: int | None
    profile_completeness: int
    missing_profile_labels: tuple[str, ...]
    next_check_in_date: date | None


@dataclass(frozen=True, slots=True)
class CredentialSummary:
    """入住凭证的页面元数据，不包含密文、指南或私有文件编号。"""

    version: int
    updated_at: datetime


TERMINAL_STAY_STATUSES = (
    "canceled",
    "cancelled",
    "declined",
    "expired",
    "deleted",
)
PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "房源名称"),
    ("room_number", "真实房间号"),
    ("room_type", "房型"),
    ("district", "区域"),
    ("address_hint", "地址提示"),
    ("parking_instructions", "停车说明"),
)


class PropertyAdminService:
    """管理房源资料和用途隔离加密的入住凭证。"""

    _private_file_pattern = re.compile(
        r"^[0-9a-f]{32}\.(?:png|jpg|webp)$"
    )

    def __init__(
        self,
        session: AsyncSession,
        cipher: SensitiveDataCipher,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        """绑定数据库事务和独立敏感数据密钥。"""
        self._session = session
        self._cipher = cipher
        self._today = today

    async def list_all(self, administrator: Employee) -> list[PropertyOverview]:
        """批量返回全部房源的资料健康度和本地运营事实。"""
        self.require_admin(administrator)
        rows = list(
            (
                await self._session.execute(
                    select(PropertyProfile, RoomOperationalState.status)
                    .outerjoin(
                        RoomOperationalState,
                        RoomOperationalState.property_id == PropertyProfile.id,
                    )
                    .order_by(PropertyProfile.title, PropertyProfile.id)
                )
            ).all()
        )
        property_rows = [(row[0], row[1]) for row in rows]
        return await self._overviews_for(property_rows, self._today())

    async def detail_for(
        self,
        property_id: int,
        administrator: Employee,
    ) -> dict[str, object]:
        """返回房源资料及当前凭证版本，绝不解密凭证正文。"""
        self.require_admin(administrator)
        property_profile = await self._session.get(
            PropertyProfile,
            property_id,
        )
        if property_profile is None:
            raise LookupError("房源不存在")
        credential_row = (
            await self._session.execute(
                select(RoomCredential.version, RoomCredential.updated_at)
                .where(
                    RoomCredential.property_id == property_id,
                    RoomCredential.is_active.is_(True),
                )
                .order_by(RoomCredential.version.desc())
                .limit(1)
            )
        ).one_or_none()
        state = await self._session.scalar(
            select(RoomOperationalState.status).where(
                RoomOperationalState.property_id == property_id
            )
        )
        overviews = await self._overviews_for(
            [(property_profile, state)],
            self._today(),
        )
        credential = (
            CredentialSummary(
                version=int(credential_row.version),
                updated_at=credential_row.updated_at,
            )
            if credential_row is not None
            else None
        )
        return {
            "property": property_profile,
            "credential": credential,
            "overview": overviews[0],
        }

    async def _overviews_for(
        self,
        property_rows: Sequence[
            tuple[PropertyProfile, RoomOperationalStatus | None]
        ],
        local_date: date,
    ) -> list[PropertyOverview]:
        """按房源集合批量聚合订单、任务和凭证，避免逐房查询。"""
        if not property_rows:
            return []
        property_ids = [profile.id for profile, _status in property_rows]
        active_status = func.lower(func.trim(StayOrder.status)).not_in(
            TERMINAL_STAY_STATUSES
        )
        stay_rows = (
            await self._session.execute(
                select(
                    StayOrder.property_id,
                    StayOrder.check_in_date,
                    StayOrder.check_out_date,
                ).where(
                    StayOrder.property_id.in_(property_ids),
                    active_status,
                    StayOrder.check_out_date >= local_date,
                )
            )
        ).all()
        task_rows = (
            await self._session.execute(
                select(BusinessTask.property_id, func.count(BusinessTask.id))
                .where(
                    BusinessTask.property_id.in_(property_ids),
                    BusinessTask.status.not_in(
                        (
                            BusinessTaskStatus.COMPLETED,
                            BusinessTaskStatus.CANCELLED,
                        )
                    ),
                )
                .group_by(BusinessTask.property_id)
            )
        ).all()
        credential_rows = (
            await self._session.execute(
                select(
                    RoomCredential.property_id,
                    func.max(RoomCredential.version),
                )
                .where(
                    RoomCredential.property_id.in_(property_ids),
                    RoomCredential.is_active.is_(True),
                )
                .group_by(RoomCredential.property_id)
            )
        ).all()

        events: dict[int, set[str]] = {
            property_id: set() for property_id in property_ids
        }
        next_check_ins: dict[int, date] = {}
        for property_id, check_in_date, check_out_date in stay_rows:
            if check_in_date == local_date:
                events[property_id].add("今日入住")
            if check_out_date == local_date:
                events[property_id].add("今日退房")
            if check_in_date < local_date < check_out_date:
                events[property_id].add("在住")
            if check_in_date > local_date:
                previous = next_check_ins.get(property_id)
                if previous is None or check_in_date < previous:
                    next_check_ins[property_id] = check_in_date

        task_counts = {
            property_id: int(count) for property_id, count in task_rows
        }
        credential_versions = {
            property_id: int(version)
            for property_id, version in credential_rows
            if version is not None
        }
        event_order = ("今日退房", "今日入住", "在住")
        return [
            self._build_overview(
                profile,
                status or RoomOperationalStatus.NOT_STARTED,
                tuple(
                    label
                    for label in event_order
                    if label in events[profile.id]
                ),
                task_counts.get(profile.id, 0),
                credential_versions.get(profile.id),
                next_check_ins.get(profile.id),
            )
            for profile, status in property_rows
        ]

    @staticmethod
    def _build_overview(
        profile: PropertyProfile,
        operational_status: RoomOperationalStatus,
        today_stay_labels: tuple[str, ...],
        open_task_count: int,
        credential_version: int | None,
        next_check_in_date: date | None,
    ) -> PropertyOverview:
        """将数据库对象转换为不含敏感字段的稳定页面模型。"""
        missing = tuple(
            label
            for field_name, label in PROFILE_FIELDS
            if not str(getattr(profile, field_name, "") or "").strip()
        )
        completed = len(PROFILE_FIELDS) - len(missing)
        return PropertyOverview(
            id=profile.id,
            title=profile.title,
            room_number=profile.room_number,
            room_type=profile.room_type,
            district=profile.district,
            address_hint=profile.address_hint,
            parking_instructions=profile.parking_instructions,
            is_active=profile.is_active,
            operational_status=operational_status,
            today_stay_labels=today_stay_labels,
            open_task_count=open_task_count,
            credential_version=credential_version,
            profile_completeness=completed * 100 // len(PROFILE_FIELDS),
            missing_profile_labels=missing,
            next_check_in_date=next_check_in_date,
        )

    async def update_profile(
        self,
        property_id: int,
        administrator: Employee,
        fields: PropertyFields,
    ) -> PropertyProfile:
        """锁定房源、校验字段并保存公开运营资料。"""
        self.require_admin(administrator)
        property_profile = await self._require_property_for_update(property_id)
        cleaned = self._clean_fields(fields)
        property_profile.title = cleaned.title
        property_profile.room_number = cleaned.room_number or None
        property_profile.room_type = cleaned.room_type or None
        property_profile.district = cleaned.district or None
        property_profile.address_hint = cleaned.address_hint or None
        property_profile.parking_instructions = (
            cleaned.parking_instructions or None
        )
        property_profile.is_active = cleaned.is_active
        self._add_audit(
            administrator.id,
            "property_profile_updated",
            property_id,
            {"is_active": cleaned.is_active},
        )
        await self._session.flush()
        return property_profile

    async def replace_credentials(
        self,
        property_id: int,
        administrator: Employee,
        *,
        password: str,
        guide: str,
        qr_file_id: str,
    ) -> RoomCredential:
        """串行创建新版加密凭证并停用该房源旧版本。"""
        self.require_admin(administrator)
        await self._require_property_for_update(property_id)
        clean_password = password.strip()
        clean_guide = guide.strip()
        if not clean_password or len(clean_password) > 128:
            raise ValueError("房间密码不能为空且不得超过 128 个字符")
        if not clean_guide or len(clean_guide) > 10000:
            raise ValueError("入住指南不能为空且不得超过 10000 个字符")
        if self._private_file_pattern.fullmatch(qr_file_id) is None:
            raise ValueError("入住二维码文件编号无效")

        active_credentials = list(
            (
                await self._session.scalars(
                    select(RoomCredential)
                    .where(
                        RoomCredential.property_id == property_id,
                        RoomCredential.is_active.is_(True),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for credential in active_credentials:
            credential.is_active = False
        latest_version = await self._session.scalar(
            select(func.max(RoomCredential.version)).where(
                RoomCredential.property_id == property_id
            )
        )
        credential = RoomCredential(
            property_id=property_id,
            version=(latest_version or 0) + 1,
            password_ciphertext=self._cipher.encrypt(
                clean_password,
                purpose="room_password",
            ),
            guide_ciphertext=self._cipher.encrypt(
                clean_guide,
                purpose="checkin_guide",
            ),
            qr_file_id=qr_file_id,
            is_active=True,
        )
        self._session.add(credential)
        await self._session.flush()
        self._add_audit(
            administrator.id,
            "room_credential_replaced",
            property_id,
            {"version": credential.version},
        )
        await self._session.flush()
        return credential

    async def active_qr_file_id(
        self,
        property_id: int,
        administrator: Employee,
    ) -> str:
        """只向启用管理员返回当前二维码的私有文件编号。"""
        self.require_admin(administrator)
        qr_file_id = await self._session.scalar(
            select(RoomCredential.qr_file_id)
            .where(
                RoomCredential.property_id == property_id,
                RoomCredential.is_active.is_(True),
            )
            .order_by(RoomCredential.version.desc())
            .limit(1)
        )
        if not isinstance(qr_file_id, str):
            raise LookupError("房源尚未配置入住凭证")
        return qr_file_id

    @staticmethod
    def require_admin(administrator: Employee) -> None:
        """拒绝停用员工和普通员工执行房源管理。"""
        if (
            not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("只有管理员可以管理房源")

    async def _require_property_for_update(
        self,
        property_id: int,
    ) -> PropertyProfile:
        """锁定并返回房源，串行化资料和凭证版本更新。"""
        property_profile = await self._session.scalar(
            select(PropertyProfile)
            .where(PropertyProfile.id == property_id)
            .with_for_update()
        )
        if property_profile is None:
            raise LookupError("房源不存在")
        return property_profile

    @staticmethod
    def _clean_fields(fields: PropertyFields) -> PropertyFields:
        """去除首尾空白并限制数据库字段长度。"""
        cleaned = PropertyFields(
            title=fields.title.strip(),
            room_type=fields.room_type.strip(),
            district=fields.district.strip(),
            address_hint=fields.address_hint.strip(),
            parking_instructions=fields.parking_instructions.strip(),
            is_active=fields.is_active,
            room_number=fields.room_number.strip(),
        )
        if not cleaned.title or len(cleaned.title) > 128:
            raise ValueError("房源名称不能为空且不得超过 128 个字符")
        if len(cleaned.room_number) > 32:
            raise ValueError("房间号不得超过 32 个字符")
        if len(cleaned.room_type) > 128 or len(cleaned.district) > 64:
            raise ValueError("房型或区域内容过长")
        if (
            len(cleaned.address_hint) > 2000
            or len(cleaned.parking_instructions) > 4000
        ):
            raise ValueError("地址提示或停车说明内容过长")
        return cleaned

    def _add_audit(
        self,
        employee_id: int,
        action: str,
        property_id: int,
        details: dict[str, object],
    ) -> None:
        """审计只保存房源编号、版本和启用状态。"""
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="property_profile",
                target_id=str(property_id),
                details={
                    "property_id": property_id,
                    **details,
                },
            )
        )
