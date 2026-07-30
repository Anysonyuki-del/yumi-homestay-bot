import re
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    PropertyProfile,
    RoomCredential,
)
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.properties import router as properties_router
from homestay_bot.services.private_file_storage import StoredPrivateFile
from homestay_bot.services.property_admin_service import (
    PropertyAdminService,
    PropertyFields,
)
from homestay_bot.services.sensitive_data import SensitiveDataCipher

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class EmployeeAuthStub:
    """返回指定角色的测试员工。"""

    def __init__(self, role: EmployeeRole) -> None:
        """保存登录角色。"""
        self.role = role

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """返回包含 OAuth state 的测试地址。"""
        return f"https://wecom.example/authorize?state={state}"

    async def authenticate(self, code: str) -> Employee:
        """返回启用员工。"""
        return Employee(
            id=1 if self.role is EmployeeRole.ADMIN else 2,
            wecom_userid="property-user",
            name="房源管理员",
            role=self.role,
            is_active=True,
        )


class PropertyAdminStub:
    """模拟管理员房源服务并记录写操作。"""

    def __init__(self, tmp_path) -> None:
        """初始化房源、凭证状态和测试二维码。"""
        self.property = SimpleNamespace(
            id=101,
            title="长江中心",
            room_type="江景大床房",
            district="武昌区",
            address_hint="地铁站附近",
            parking_instructions="停车前联系管理员",
            is_active=True,
        )
        self.credential = SimpleNamespace(version=3, is_active=True)
        self.profile_calls: list[dict[str, object]] = []
        self.credential_calls: list[dict[str, object]] = []
        qr_path = tmp_path / ("a" * 32 + ".png")
        qr_path.write_bytes(PNG_BYTES)
        self.qr = StoredPrivateFile(
            file_id=qr_path.name,
            path=qr_path,
            content_type="image/png",
            size=len(PNG_BYTES),
        )

    @staticmethod
    def _require_admin(employee) -> None:
        """拒绝普通员工进入管理服务。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以管理房源")

    async def list_all(self, employee):
        """返回管理员可见房源。"""
        self._require_admin(employee)
        return [self.property]

    async def detail_for(self, property_id, employee):
        """返回不含凭证明文的房源详情。"""
        self._require_admin(employee)
        assert property_id == 101
        return {
            "property": self.property,
            "credential": self.credential,
        }

    async def update_profile(self, property_id, employee, fields):
        """记录房源资料更新。"""
        self._require_admin(employee)
        self.profile_calls.append(
            {
                "property_id": property_id,
                "employee_id": employee.id,
                "fields": fields,
            }
        )
        return self.property

    async def replace_credentials(
        self,
        property_id,
        employee,
        password,
        guide,
        stream,
        content_type,
    ):
        """记录凭证及二维码上传。"""
        self._require_admin(employee)
        self.credential_calls.append(
            {
                "property_id": property_id,
                "employee_id": employee.id,
                "password": password,
                "guide": guide,
                "content": stream.read(),
                "content_type": content_type,
            }
        )
        return self.credential

    async def qr_for(self, property_id, employee):
        """只向管理员返回测试二维码。"""
        self._require_admin(employee)
        assert property_id == 101
        return self.qr


def build_client(role: EmployeeRole, tmp_path) -> tuple[TestClient, PropertyAdminStub]:
    """创建带签名员工会话的房源管理测试应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="property-test-secret")
    app.include_router(employee_auth_router)
    app.include_router(properties_router)
    app.state.employee_auth_service = EmployeeAuthStub(role)
    service = PropertyAdminStub(tmp_path)
    app.state.property_admin_service = service
    return TestClient(app), service


def login(client: TestClient) -> None:
    """走 OAuth state 流程建立员工会话。"""
    response = client.get(
        "/employee/login",
        params={"next": "/employee/properties"},
        follow_redirects=False,
    )
    state = re.search(r"state=([^&]+)", response.headers["location"]).group(1)
    callback = client.get(
        "/employee/oauth/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303


def detail_csrf(client: TestClient) -> str:
    """从房源详情读取一次性 CSRF 令牌。"""
    response = client.get("/employee/properties/101")
    return re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.text,
    ).group(1)


def test_staff_cannot_access_property_admin(tmp_path) -> None:
    """普通员工不得查看或修改房源配置。"""
    client, _ = build_client(EmployeeRole.STAFF, tmp_path)
    login(client)

    response = client.get("/employee/properties")

    assert response.status_code == 403


def test_admin_page_never_echoes_room_password(tmp_path) -> None:
    """管理员详情只显示凭证版本，不回显密码或指南明文。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)

    response = client.get("/employee/properties/101")

    assert response.status_code == 200
    assert "凭证版本 3" in response.text
    assert "839201" not in response.text
    assert 'name="password"' in response.text
    assert 'name="password" value=' not in response.text


def test_admin_updates_profile_and_replaces_credentials(tmp_path) -> None:
    """管理员可通过一次性令牌更新资料并上传新版私有凭证。"""
    client, service = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)
    profile_token = detail_csrf(client)
    profile = client.post(
        "/employee/properties/101/profile",
        data={
            "title": "长江中心 101",
            "room_type": "江景大床房",
            "district": "武昌区",
            "address_hint": "地铁站附近",
            "parking_instructions": "停车前联系管理员",
            "is_active": "true",
            "csrf_token": profile_token,
        },
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/properties/101/profile",
        data={
            "title": "重放不应生效",
            "csrf_token": profile_token,
        },
        follow_redirects=False,
    )
    credential_token = detail_csrf(client)
    credential = client.post(
        "/employee/properties/101/credentials",
        data={
            "password": "839201",
            "guide": "入住后请先核对房号。",
            "csrf_token": credential_token,
        },
        files={"qr_image": ("checkin.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )

    assert profile.status_code == 303
    assert replay.status_code == 409
    assert credential.status_code == 303
    assert service.profile_calls[0]["property_id"] == 101
    assert service.credential_calls[0]["content"] == PNG_BYTES


def test_private_qr_requires_admin_session(tmp_path) -> None:
    """入住二维码不能通过公开地址或普通员工会话读取。"""
    admin, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    login(admin)
    staff, _ = build_client(EmployeeRole.STAFF, tmp_path)
    login(staff)

    visible = admin.get("/employee/properties/101/qr")
    forbidden = staff.get("/employee/properties/101/qr")

    assert visible.status_code == 200
    assert visible.content == PNG_BYTES
    assert visible.headers["cache-control"] == "no-store"
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_credentials_are_versioned_encrypted_and_safely_audited() -> None:
    """新版凭证必须加密、绑定房间并且审计不复制任何明文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        admin = Employee(
            wecom_userid="property-admin",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        room = PropertyProfile(id=101, title="长江中心")
        session.add_all([admin, room])
        await session.flush()
        service = PropertyAdminService(session, cipher)

        await service.update_profile(
            101,
            admin,
            PropertyFields(
                title="长江中心 101",
                room_type="江景大床房",
                district="武昌区",
                address_hint="地铁站附近",
                parking_instructions="停车前联系管理员",
                is_active=True,
            ),
        )
        first = await service.replace_credentials(
            101,
            admin,
            password="839201",
            guide="入住后请先核对房号。",
            qr_file_id="a" * 32 + ".png",
        )
        second = await service.replace_credentials(
            101,
            admin,
            password="528630",
            guide="新版入住指南。",
            qr_file_id="b" * 32 + ".png",
        )
        await session.commit()
        credentials = list(
            (
                await session.scalars(
                    select(RoomCredential).order_by(RoomCredential.version)
                )
            ).all()
        )
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.target_type == "property_profile"
                    )
                )
            ).all()
        )

        assert first.version == 1
        assert first.is_active is False
        assert second.version == 2
        assert second.is_active is True
        assert second.property_id == 101
        assert b"528630" not in second.password_ciphertext
        assert cipher.decrypt(
            second.password_ciphertext,
            purpose="room_password",
        ) == "528630"
        assert cipher.decrypt(
            second.guide_ciphertext,
            purpose="checkin_guide",
        ) == "新版入住指南。"
        assert len(credentials) == 2
        assert "839201" not in str([item.details for item in audits])
        assert "入住后" not in str([item.details for item in audits])

    await engine.dispose()
