"""房间介绍端到端（小程序住客自助 Spec F2–F4）：后台经真实路由与服务写入，公开接口读回。

真实装配：SessionPropertyAdminService、MiniProgramContentService、私有文件存储、临时 SQLite，
与 main.py 相同的 no-store 中间件；只有管理员认证用测试替身。
"""

import asyncio
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.application import SessionPropertyAdminService
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    Job,
    PropertyProfile,
    RoomShowcasePhoto,
)
from homestay_bot.middleware import AdminNoStoreMiddleware
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.miniprogram_content import router as content_router
from homestay_bot.routes.properties import router as properties_router
from homestay_bot.services.miniprogram_content import MiniProgramContentService
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def _png(marker: bytes = b"") -> bytes:
    """最小 PNG；可选带一个含标记文字的 tEXt 块，用来检查元数据是否被去掉。"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    text = chunk(b"tEXt", b"GPS\x00" + marker) if marker else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + text
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


@dataclass
class World:
    """一次测试的客户端、数据库与私有目录。"""

    client: TestClient
    factory: async_sessionmaker[Any]
    upload_dir: Path

    def csrf(self, property_id: int = 101) -> str:
        """从小程序展示页签读取一次性令牌。"""
        page = self.client.get(f"/employee/properties/{property_id}?tab=showcase")
        assert page.status_code == 200, page.text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert match is not None
        return match.group(1)

    def save(self, property_id: int = 101, **fields: str) -> Any:
        """提交小程序介绍表单；默认勾选展示。"""
        data = {"is_visible": "true", "csrf_token": self.csrf(property_id), **fields}
        return self.client.post(
            f"/employee/properties/{property_id}/showcase", data=data, follow_redirects=False
        )

    def upload(
        self, content: bytes, property_id: int = 101, content_type: str = "image/png"
    ) -> Any:
        """上传一张照片。"""
        return self.client.post(
            f"/employee/properties/{property_id}/showcase/photos",
            data={"csrf_token": self.csrf(property_id)},
            files={"image": ("photo.png", content, content_type)},
            follow_redirects=False,
        )

    def query(self, statement: Any) -> list[Any]:
        """在独立事件循环里读一次数据库。"""

        async def run() -> list[Any]:
            async with self.factory() as session:
                return list(await session.scalars(statement))

        return asyncio.run(run())

    def files(self) -> list[str]:
        """私有目录里的图片文件。"""
        return sorted(path.name for path in self.upload_dir.iterdir())


@pytest.fixture
def world(tmp_path: Path) -> World:
    """建库、放入管理员和两间房，装配后台与公开接口。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'showcase.db'}", poolclass=NullPool
    )

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            session.add_all(
                [
                    Employee(
                        id=1,
                        wecom_userid="synthetic-admin",
                        name="合成管理员",
                        role=EmployeeRole.ADMIN,
                        is_active=True,
                    ),
                    PropertyProfile(
                        id=101,
                        title="A栋301-内部编号",
                        room_number="301",
                        address_hint="合成地址提示",
                        parking_instructions="合成停车说明",
                    ),
                    PropertyProfile(id=102, title="山景房"),
                ]
            )
            await session.commit()

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    upload_dir = tmp_path / "private"
    storage = PrivateFileStorage(upload_dir)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="showcase-test-secret")
    app.add_middleware(AdminNoStoreMiddleware)
    app.include_router(employee_auth_router)
    app.include_router(properties_router)
    app.include_router(content_router)
    configure_admin_auth(app, EmployeeRole.ADMIN)
    app.state.property_admin_service = SessionPropertyAdminService(
        factory, SensitiveDataCipher(Fernet.generate_key().decode("ascii")), storage, 1 << 22
    )
    app.state.miniprogram_content_service = MiniProgramContentService(factory, storage)
    client = TestClient(app)
    login_admin(client, next_path="/employee/properties")
    return World(client=client, factory=factory, upload_dir=upload_dir)


def test_saved_room_is_public_with_whitelisted_fields(world: World) -> None:
    """保存后列表与详情都能读到；名称回退到房间标题；不含地址、停车、房号。"""
    response = world.save(
        intro="朝南，有阳台。",
        bed_type="1.8 米大床",
        max_guests="2",
        area_sqm="35",
        facilities="洗衣机\n\n投影\n洗衣机",
    )
    assert response.status_code == 303
    rooms = world.client.get("/api/miniprogram/rooms").json()["rooms"]
    assert rooms == [
        {
            "id": 101,
            "name": "A栋301-内部编号",
            "bed_type": "1.8 米大床",
            "max_guests": 2,
            "area_sqm": 35,
            "cover_url": None,
        }
    ]
    detail = world.client.get("/api/miniprogram/rooms/101")
    assert detail.status_code == 200
    body = detail.json()
    assert body["intro"] == "朝南，有阳台。"
    assert body["facilities"] == ["洗衣机", "投影"]
    assert body["photos"] == []
    for leaked in ("合成地址提示", "合成停车说明", '301"'):
        assert leaked not in detail.text
    assert detail.headers["cache-control"] == "no-store"


def test_display_name_overrides_title(world: World) -> None:
    """填了展示名称就不再显示内部标题。"""
    world.save(display_name="山海大床房")
    assert world.client.get("/api/miniprogram/rooms").json()["rooms"][0]["name"] == "山海大床房"


@pytest.mark.parametrize("reason", ["not-ticked", "room-inactive"])
def test_hidden_room_disappears_everywhere(world: World, reason: str) -> None:
    """未勾选展示或房源停用：列表、详情、照片都当作不存在。"""
    world.save()
    world.upload(_png())
    photo_id = world.query(select(RoomShowcasePhoto.id))[0]
    if reason == "not-ticked":
        world.client.post("/employee/properties/101/showcase", data={"csrf_token": world.csrf()})
    else:

        async def deactivate() -> None:
            async with world.factory() as session:
                (await session.get(PropertyProfile, 101)).is_active = False
                await session.commit()

        asyncio.run(deactivate())
    assert world.client.get("/api/miniprogram/rooms").json() == {"rooms": []}
    assert world.client.get("/api/miniprogram/rooms/101").status_code == 404
    photo = world.client.get(f"/api/miniprogram/rooms/101/photos/{photo_id}")
    assert photo.status_code == 404
    # 照片路径不走统一 no-store，失败结果必须自己声明不缓存。
    assert photo.headers["cache-control"] == "no-store"


def test_rooms_sort_by_order_with_blank_last(world: World) -> None:
    """排序号升序，未填的排在最后。"""
    world.save(property_id=101)
    world.save(property_id=102, sort_order="5")
    assert [room["id"] for room in world.client.get("/api/miniprogram/rooms").json()["rooms"]] == [
        102,
        101,
    ]


def test_uploaded_photo_is_stripped_cached_and_scoped_to_its_room(world: World) -> None:
    """照片去掉元数据后公开可读并允许缓存；换房间编号或用文件名都读不到。"""
    world.save()
    world.save(property_id=102)
    assert world.upload(_png(b"30.5N")).status_code == 303
    photo_id = world.query(select(RoomShowcasePhoto.id))[0]
    room = world.client.get("/api/miniprogram/rooms/101").json()
    assert room["photos"] == [f"/api/miniprogram/rooms/101/photos/{photo_id}"]
    assert room["cover_url"] == room["photos"][0]

    photo = world.client.get(room["photos"][0])
    assert photo.status_code == 200
    assert photo.content == _png()
    assert photo.headers["cache-control"] == "public, max-age=86400"
    assert photo.headers["x-content-type-options"] == "nosniff"

    assert world.client.get(f"/api/miniprogram/rooms/102/photos/{photo_id}").status_code == 404
    file_id = world.files()[0]
    assert world.client.get(f"/api/miniprogram/rooms/101/photos/{file_id}").status_code == 422


def test_photo_limit_and_invalid_images_leave_no_files(world: World) -> None:
    """第 10 张被拒；伪造图片被拒；被拒的上传都不留下文件。"""
    for _ in range(9):
        assert world.upload(_png()).status_code == 303
    assert world.upload(_png()).status_code == 400
    assert world.upload(b"\x89PNG\r\n\x1a\nbroken").status_code == 400
    assert len(world.files()) == 9


def test_delete_and_reorder_photos(world: World) -> None:
    """删除登记文件清理任务；排序改变封面；别的房间的照片编号整批拒绝。"""
    world.save()
    world.save(property_id=102)
    for _ in range(2):
        world.upload(_png())
    world.upload(_png(), property_id=102)
    first, second, foreign = world.query(
        select(RoomShowcasePhoto.id).order_by(RoomShowcasePhoto.id)
    )

    reordered = world.client.post(
        "/employee/properties/101/showcase/photos/order",
        data={"csrf_token": world.csrf(), f"order_{first}": "9", f"order_{second}": "1"},
        follow_redirects=False,
    )
    assert reordered.status_code == 303
    cover = world.client.get("/api/miniprogram/rooms").json()["rooms"][0]["cover_url"]
    assert cover.endswith(f"/photos/{second}")

    cross_room = world.client.post(
        "/employee/properties/101/showcase/photos/order",
        data={"csrf_token": world.csrf(), f"order_{first}": "0", f"order_{foreign}": "0"},
    )
    assert cross_room.status_code == 404

    file_id = world.query(select(RoomShowcasePhoto.file_id).where(RoomShowcasePhoto.id == second))[
        0
    ]
    deleted = world.client.post(
        f"/employee/properties/101/showcase/photos/{second}/delete",
        data={"csrf_token": world.csrf()},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    jobs = world.query(select(Job).where(Job.job_type == "task_attachment_cleanup"))
    assert [job.payload["file_ids"] for job in jobs] == [[file_id]]
    assert world.client.get(f"/api/miniprogram/rooms/101/photos/{second}").status_code == 404


@pytest.mark.parametrize(
    "fields",
    [
        {"max_guests": "21"},
        {"area_sqm": "0"},
        {"sort_order": "-1"},
        {"display_name": "名" * 65},
        {"intro": "介" * 2001},
        {"facilities": "\n".join(f"设施{index}" for index in range(31))},
        {"facilities": "超" * 21},
    ],
    ids=["guests", "area", "sort", "name", "intro", "facility-count", "facility-length"],
)
def test_out_of_range_fields_are_rejected(world: World, fields: dict[str, str]) -> None:
    """越界字段整单拒绝，不写入半份资料。"""
    assert world.save(**fields).status_code == 400
    assert world.client.get("/api/miniprogram/rooms").json() == {"rooms": []}


def test_writes_need_csrf_and_audit_omits_body(world: World) -> None:
    """缺令牌拒绝；审计只记动作和是否展示，不复制介绍正文。"""
    rejected = world.client.post(
        "/employee/properties/101/showcase",
        data={"is_visible": "true", "csrf_token": "forged"},
    )
    # 本项目令牌失效统一返回 409，提示刷新页面。
    assert rejected.status_code == 409
    assert world.client.get("/api/miniprogram/rooms").json() == {"rooms": []}
    world.save(intro="私密的介绍正文")
    audits = world.query(select(AuditLog).where(AuditLog.action == "room_showcase_updated"))
    assert len(audits) == 1
    assert "私密" not in str(audits[0].details)


def test_staff_cannot_open_or_write_showcase(tmp_path: Path) -> None:
    """普通员工既看不到也改不了小程序展示。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="showcase-test-secret")
    app.include_router(employee_auth_router)
    app.include_router(properties_router)
    configure_admin_auth(app, EmployeeRole.STAFF)
    app.state.property_admin_service = object()
    client = TestClient(app)
    login_admin(client, next_path="/employee/properties")
    assert client.get("/employee/properties/101?tab=showcase").status_code == 403
    assert (
        client.post("/employee/properties/101/showcase", data={"csrf_token": "x"}).status_code
        == 403
    )
