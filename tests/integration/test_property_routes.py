import logging
import re
from types import SimpleNamespace

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole, RoomOperationalStatus
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


class PropertyAdminStub:
    """模拟管理员房源服务并记录写操作。"""

    def __init__(self, tmp_path) -> None:
        """初始化房源、凭证状态和测试二维码。"""
        self.property = SimpleNamespace(
            id=101,
            title="长江中心",
            room_number=None,
            room_type="江景大床房",
            district="武昌区",
            address_hint="地铁站附近",
            parking_instructions="停车前联系管理员",
            is_active=True,
            operational_status=RoomOperationalStatus.READY,
            today_stay_labels=("今日入住",),
            open_task_count=2,
            credential_version=3,
            profile_completeness=67,
            missing_profile_labels=("真实房间号", "停车说明"),
            next_check_in_date=None,
        )
        self.credential = SimpleNamespace(version=3, is_active=True)
        self.profile_calls: list[dict[str, object]] = []
        self.credential_calls: list[dict[str, object]] = []
        self.detail_error: Exception | None = None
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
        if self.detail_error is not None:
            raise self.detail_error
        assert property_id == 101
        return {
            "property": self.property,
            "credential": self.credential,
            "overview": self.property,
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
    configure_admin_auth(app, role)
    service = PropertyAdminStub(tmp_path)
    app.state.property_admin_service = service
    return TestClient(app), service


def login(client: TestClient) -> None:
    """通过独立账号密码表单建立版本化员工会话。"""
    login_admin(client, next_path="/employee/properties")


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


def test_property_unknown_error_uses_stable_detail_and_safe_log(
    tmp_path,
    caplog,
) -> None:
    """房源未知异常不得把内部异常原文返回给页面。"""
    client, service = build_client(EmployeeRole.ADMIN, tmp_path)
    service.detail_error = RuntimeError("secret SQL value")
    login(client)

    with caplog.at_level(logging.ERROR):
        response = client.get("/employee/properties/101")

    assert response.status_code == 409
    assert response.json()["detail"] == "房源管理操作未完成"
    assert "secret SQL value" not in response.text
    assert any(
        record.getMessage().startswith("房源管理操作失败")
        and "RuntimeError" in record.getMessage()
        for record in caplog.records
    )


def test_admin_page_never_echoes_room_password(tmp_path) -> None:
    """管理员详情只显示凭证版本，不回显密码或指南明文。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)

    response = client.get("/employee/properties/101?tab=credentials")

    assert response.status_code == 200
    assert "凭证版本 3" in response.text
    assert "839201" not in response.text
    assert 'name="password"' in response.text
    assert 'name="password" value=' not in response.text


def test_property_pages_use_admin_shell_and_protect_credential_replacement(
    tmp_path,
) -> None:
    """房源页面应激活导航，并警告未保存资料和凭证替换风险。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)

    index = client.get("/employee/properties")
    detail = client.get("/employee/properties/101?tab=credentials")
    profile = client.get("/employee/properties/101?tab=profile")

    assert '/static/admin.js' in index.text
    assert 'href="/employee/properties" aria-current="page"' in detail.text
    assert 'class="data-table"' in index.text
    assert 'class="mobile-card-list clean-list"' in index.text
    assert detail.text.count('data-unsaved-warning') == 1
    assert (
        'action="/employee/properties/101/credentials" data-confirm='
        in detail.text
    )
    assert 'name="password" value=' not in detail.text
    assert 'name="guide">入住后' not in detail.text
    assert 'name="password" maxlength="256"' in detail.text
    assert 'name="room_number" value="" maxlength="64"' in profile.text


def test_property_index_shows_operational_health_summary(tmp_path) -> None:
    """房源列表直接呈现运营、任务、凭证和资料完整度。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)

    response = client.get("/employee/properties")

    assert response.status_code == 200
    assert "可入住" in response.text
    assert "今日入住" in response.text
    assert "2 项待处理" in response.text
    assert "凭证 v3" in response.text
    assert "资料完整度 67%" in response.text


def test_property_detail_uses_url_tabs_and_keeps_writes_separate(tmp_path) -> None:
    """详情默认只读，资料和凭证表单只出现在对应 URL 页签。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)

    overview = client.get("/employee/properties/101")
    profile = client.get("/employee/properties/101?tab=profile")
    credentials = client.get("/employee/properties/101?tab=credentials")

    assert overview.status_code == 200
    assert 'aria-current="page">运营概览' in overview.text
    assert 'action="/employee/properties/101/profile"' not in overview.text
    assert 'action="/employee/properties/101/credentials"' not in overview.text
    assert 'aria-current="page">公开资料' in profile.text
    assert 'action="/employee/properties/101/profile"' in profile.text
    assert 'action="/employee/properties/101/credentials"' not in profile.text
    assert 'aria-current="page">入住凭证' in credentials.text
    assert 'action="/employee/properties/101/credentials"' in credentials.text
    assert 'name="password" value=' not in credentials.text
    assert "入住后请先核对房号" not in credentials.text


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
    assert profile.headers["location"] == "/employee/properties/101?tab=profile"
    assert replay.status_code == 409
    assert credential.status_code == 303
    assert credential.headers["location"] == (
        "/employee/properties/101?tab=credentials"
    )
    assert service.profile_calls[0]["property_id"] == 101
    assert service.credential_calls[0]["content"] == PNG_BYTES


def test_property_route_rejects_oversized_profile_field(tmp_path) -> None:
    """超长房源标题必须在业务服务前返回 422。"""
    client, service = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)
    csrf_token = detail_csrf(client)

    response = client.post(
        "/employee/properties/101/profile",
        data={
            "title": "房" * 129,
            "room_number": "101",
            "room_type": "大床房",
            "district": "武昌区",
            "address_hint": "地铁站附近",
            "parking_instructions": "提前联系",
            "is_active": "true",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert service.profile_calls == []


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


def test_property_csrf_rejects_cross_entity_replay(tmp_path) -> None:
    """房源详情签发的令牌不得用于修改另一个房源。"""
    client, properties = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)
    csrf_token = detail_csrf(client)

    response = client.post(
        "/employee/properties/202/profile",
        data={"title": "另一处房源", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert properties.profile_calls == []


def test_room_readiness_looks_the_same_on_desktop_and_phone(tmp_path) -> None:
    """同一个房间的运营准备度，桌面和手机必须给出同一个徽标。

    此前桌面按 ready/其他 取绿或黄，手机固定用蓝色，同一间房在两块屏幕上是两
    种颜色，颜色也就不再有含义。维修更是完全没有风险表达。
    """
    client, properties = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)

    ready = client.get("/employee/properties")
    properties.property.operational_status = RoomOperationalStatus.MAINTENANCE
    maintenance = client.get("/employee/properties")

    assert ready.text.count('<span class="badge badge--success">运营：可入住</span>') == 2
    assert maintenance.text.count('<span class="badge badge--danger">运营：维修中</span>') == 2
    assert "badge--info" not in maintenance.text


def test_property_detail_reuses_the_same_readiness_badge(tmp_path) -> None:
    """房源详情不得给运营准备度另立一套颜色规则。"""
    client, properties = build_client(EmployeeRole.ADMIN, tmp_path)
    login(client)
    properties.property.operational_status = RoomOperationalStatus.MAINTENANCE

    response = client.get("/employee/properties/101")

    assert '<span class="badge badge--danger">运营：维修中</span>' in response.text


class RoomReadinessStub:
    """记录管理员直接设定房态的调用。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[int, str]] = []

    async def set_status_by_admin(self, property_id, administrator, room_status):
        """接受任意状态并记录。"""
        self.calls.append((property_id, room_status.value))
        return None


OPERATIONS_SOURCE = "/employee/admin/operations?days=7#room-101"


def test_room_status_from_operations_page_returns_to_that_view(tmp_path) -> None:
    """在运营页改房态后要留在原调度视图，而不是被甩进房间详情。

    老板跨房间调度时，此前更新一间就丢掉运营页和 3/7/14 天范围，得一路返回
    重新选。锚点也要保留：一屏几十间房，回到页首等于再找一遍。
    """
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    client.app.state.room_readiness_service = RoomReadinessStub()
    login(client)
    token = detail_csrf(client)

    response = client.post(
        "/employee/properties/101/room-status",
        data={
            "csrf_token": token,
            "room_status": "ready",
            "return_to": OPERATIONS_SOURCE,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == OPERATIONS_SOURCE


def test_room_status_without_a_source_still_lands_on_the_room(tmp_path) -> None:
    """房间详情内提交没有来源，兜底必须是该房间而不是任务中心。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    client.app.state.room_readiness_service = RoomReadinessStub()
    login(client)
    token = detail_csrf(client)

    response = client.post(
        "/employee/properties/101/room-status",
        data={"csrf_token": token, "room_status": "ready"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/employee/properties/101"


def test_room_status_refuses_a_foreign_source(tmp_path) -> None:
    """站外 return_to 必须回落到该房间，不得成为开放重定向。"""
    client, _ = build_client(EmployeeRole.ADMIN, tmp_path)
    client.app.state.room_readiness_service = RoomReadinessStub()
    login(client)
    token = detail_csrf(client)

    response = client.post(
        "/employee/properties/101/room-status",
        data={
            "csrf_token": token,
            "room_status": "ready",
            "return_to": "https://evil.example.com/steal",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/employee/properties/101"
