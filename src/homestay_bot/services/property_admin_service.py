import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import (
    AuditLog,
    Employee,
    PropertyProfile,
    RoomCredential,
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


class PropertyAdminService:
    """管理房源资料和用途隔离加密的入住凭证。"""

    _private_file_pattern = re.compile(
        r"^[0-9a-f]{32}\.(?:png|jpg|webp)$"
    )

    def __init__(
        self,
        session: AsyncSession,
        cipher: SensitiveDataCipher,
    ) -> None:
        """绑定数据库事务和独立敏感数据密钥。"""
        self._session = session
        self._cipher = cipher

    async def list_all(self, administrator: Employee) -> list[PropertyProfile]:
        """只向启用管理员返回全部房源配置。"""
        self.require_admin(administrator)
        return list(
            (
                await self._session.scalars(
                    select(PropertyProfile).order_by(
                        PropertyProfile.title,
                        PropertyProfile.id,
                    )
                )
            ).all()
        )

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
        credential = await self._session.scalar(
            select(RoomCredential)
            .where(
                RoomCredential.property_id == property_id,
                RoomCredential.is_active.is_(True),
            )
            .order_by(RoomCredential.version.desc())
            .limit(1)
        )
        return {
            "property": property_profile,
            "credential": credential,
        }

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
        detail = await self.detail_for(property_id, administrator)
        credential = detail["credential"]
        if not isinstance(credential, RoomCredential):
            raise LookupError("房源尚未配置入住凭证")
        return credential.qr_file_id

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
        )
        if not cleaned.title or len(cleaned.title) > 128:
            raise ValueError("房源名称不能为空且不得超过 128 个字符")
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
