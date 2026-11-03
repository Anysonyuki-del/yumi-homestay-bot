"""小程序首页端到端（三标签 Spec F4）：后台经真实路由与服务写门店信息和横幅，公开接口读回。

真实装配：SessionMiniProgramHomeAdmin、MiniProgramContentService、私有文件存储、临时 SQLite，
与 main.py 相同的 no-store 中间件；只有管理员认证用测试替身。门店信息行由迁移预置，
这里建表后手动插入同一行。
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    Job,
    MiniProgramBanner,
    MiniProgramStoreProfile,
)
from homestay_bot.middleware import AdminNoStoreMiddleware
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.miniprogram_admin import router as admin_router
from homestay_bot.routes.miniprogram_content import router as content_router
from homestay_bot.services.miniprogram_content import MiniProgramContentService
from homestay_bot.services.miniprogram_home_admin import SessionMiniProgramHomeAdmin
from homestay_bot.services.private_file_storage import PrivateFileStorage
from tests.integration.test_room_showcase import _png

PAGE = "/employee/admin/miniprogram"
STORE = {
    "name": "山野小住",
    "intro": "推窗见山。",
    "address": "合成地址 1 号",
    "latitude": "30.25",
    "longitude": "120.15",
    "phone": "0571-1234 5678",
}


@dataclass
class World:
    """一次测试的客户端、数据库与私有目录。"""

    client: TestClient
    factory: async_sessionmaker[Any]
    upload_dir: Path

    def csrf(self) -> str:
        """从后台页面读取一次性令牌。"""
        page = self.client.get(PAGE)
        assert page.status_code == 200, page.text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert match is not None
        return match.group(1)

    def save(self, **fields: str) -> Any:
        """提交门店信息表单。"""
        return self.client.post(
            f"{PAGE}/store", data={"csrf_token": self.csrf(), **fields}, follow_redirects=False
        )

    def upload(self, content: bytes, content_type: str = "image/png") -> Any:
        """上传一张横幅。"""
        return self.client.post(
            f"{PAGE}/banners",
            data={"csrf_token": self.csrf()},
            files={"image": ("banner.png", content, content_type)},
            follow_redirects=False,
        )

    def home(self) -> dict[str, Any]:
        """读公开首页接口。"""
        response = self.client.get("/api/miniprogram/home")
        assert response.status_code == 200, response.text
        return dict(response.json())

    def query(self, statement: Any) -> list[Any]:
        """在独立事件循环里读一次数据库。"""

        async def run() -> list[Any]:
            async with self.factory() as session:
                return list(await session.scalars(statement))

        return asyncio.run(run())

    def banner_ids(self) -> list[int]:
        """按编号列出横幅。"""
        return self.query(select(MiniProgramBanner.id).order_by(MiniProgramBanner.id))


def _app(tmp_path: Path, role: EmployeeRole) -> tuple[TestClient, async_sessionmaker[Any], Path]:
    """建库、放入员工与预置门店行，装配后台与公开接口。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'home.db'}", poolclass=NullPool)

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
                    MiniProgramStoreProfile(id=1),
                ]
            )
            await session.commit()

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    upload_dir = tmp_path / "private"
    storage = PrivateFileStorage(upload_dir)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="home-test-secret")
    app.add_middleware(AdminNoStoreMiddleware)
    app.include_router(employee_auth_router)
    app.include_router(admin_router)
    app.include_router(content_router)
    configure_admin_auth(app, role)
    app.state.miniprogram_home_admin = SessionMiniProgramHomeAdmin(factory, storage, 1 << 22)
    app.state.miniprogram_content_service = MiniProgramContentService(factory, storage)
    client = TestClient(app)
    login_admin(client, next_path=PAGE)
    return client, factory, upload_dir


@pytest.fixture
def world(tmp_path: Path) -> World:
    """管理员视角的测试环境。"""
    client, factory, upload_dir = _app(tmp_path, EmployeeRole.ADMIN)
    return World(client=client, factory=factory, upload_dir=upload_dir)


def test_unconfigured_home_is_null_and_saved_store_is_public(world: World) -> None:
    """未填门店信息时 store 为 null；保存后原样公开，坐标为数字；再清空又回到 null。"""
    assert world.home() == {"store": None, "banners": []}
    assert world.save(**STORE).status_code == 303
    response = world.client.get("/api/miniprogram/home")
    assert response.json()["store"] == {
        "name": "山野小住",
        "intro": "推窗见山。",
        "address": "合成地址 1 号",
        "latitude": 30.25,
        "longitude": 120.15,
        "phone": "0571-1234 5678",
    }
    # 首页文字随时可改，接口不能被缓存。
    assert response.headers["cache-control"] == "no-store"

    assert world.save().status_code == 303
    assert world.home()["store"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "名" * 33},
        {"intro": "介" * 301},
        {"address": "址" * 121},
        {"phone": "call-me"},
        {"latitude": "30.2", "longitude": ""},
        {"latitude": "91", "longitude": "120"},
        {"latitude": "nan", "longitude": "120"},
    ],
    ids=["name", "intro", "address", "phone", "half-coordinate", "out-of-range", "nan"],
)
def test_invalid_store_fields_are_rejected_whole(world: World, fields: dict[str, str]) -> None:
    """任一字段无效整单拒绝，已保存的内容保持不变。"""
    world.save(**STORE)
    assert world.save(**{**STORE, **fields}).status_code == 400
    assert world.home()["store"]["name"] == "山野小住"
    assert world.home()["store"]["latitude"] == 30.25


def test_banner_is_stripped_cached_and_read_only_by_banner_id(world: World) -> None:
    """横幅去掉元数据后公开可读并允许缓存；用文件名或不存在的编号读不到。"""
    assert world.upload(_png(b"30.5N")).status_code == 303
    (banner_id,) = world.banner_ids()
    assert world.home()["banners"] == [f"/api/miniprogram/banners/{banner_id}"]

    banner = world.client.get(f"/api/miniprogram/banners/{banner_id}")
    assert banner.status_code == 200
    assert banner.content == _png()
    assert banner.headers["cache-control"] == "public, max-age=86400"
    assert banner.headers["x-content-type-options"] == "nosniff"

    missing = world.client.get(f"/api/miniprogram/banners/{banner_id + 1}")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    file_id = next(world.upload_dir.iterdir()).name
    assert world.client.get(f"/api/miniprogram/banners/{file_id}").status_code == 422


def test_banner_limit_and_invalid_images_leave_no_files(world: World) -> None:
    """第 7 张被拒；伪造图片被拒；被拒的上传都不留下文件。"""
    for _ in range(6):
        assert world.upload(_png()).status_code == 303
    assert world.upload(_png()).status_code == 400
    assert world.upload(b"\x89PNG\r\n\x1a\nbroken").status_code == 400
    assert len(list(world.upload_dir.iterdir())) == 6
    assert len(world.banner_ids()) == 6


def test_reorder_and_delete_banners(world: World) -> None:
    """排序改变公开顺序；不存在的编号整批拒绝；删除登记文件清理任务。"""
    world.upload(_png())
    world.upload(_png())
    first, second = world.banner_ids()
    assert world.home()["banners"][0].endswith(f"/{first}")

    reordered = world.client.post(
        f"{PAGE}/banners/order",
        data={"csrf_token": world.csrf(), f"order_{first}": "9", f"order_{second}": "1"},
        follow_redirects=False,
    )
    assert reordered.status_code == 303
    assert [url.rsplit("/", 1)[1] for url in world.home()["banners"]] == [str(second), str(first)]

    unknown = world.client.post(
        f"{PAGE}/banners/order",
        data={"csrf_token": world.csrf(), f"order_{first}": "0", f"order_{second + 99}": "0"},
    )
    assert unknown.status_code == 404
    assert [url.rsplit("/", 1)[1] for url in world.home()["banners"]] == [str(second), str(first)]

    file_id = world.query(select(MiniProgramBanner.file_id).where(MiniProgramBanner.id == second))[
        0
    ]
    deleted = world.client.post(
        f"{PAGE}/banners/{second}/delete",
        data={"csrf_token": world.csrf()},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    jobs = world.query(select(Job).where(Job.job_type == "task_attachment_cleanup"))
    assert [job.payload["file_ids"] for job in jobs] == [[file_id]]
    assert world.client.get(f"/api/miniprogram/banners/{second}").status_code == 404
    assert world.home()["banners"] == [f"/api/miniprogram/banners/{first}"]


def test_writes_need_csrf_and_audit_omits_text(world: World) -> None:
    """伪造令牌返回 409 且不写入；审计只记动作，不复制公开文字和电话。"""
    rejected = world.client.post(f"{PAGE}/store", data={"csrf_token": "forged", **STORE})
    assert rejected.status_code == 409
    upload = world.client.post(
        f"{PAGE}/banners",
        data={"csrf_token": "forged"},
        files={"image": ("banner.png", _png(), "image/png")},
    )
    assert upload.status_code == 409
    assert world.home() == {"store": None, "banners": []}
    assert not world.upload_dir.exists() or not any(world.upload_dir.iterdir())

    world.save(**STORE)
    audits = world.query(select(AuditLog).where(AuditLog.action == "miniprogram_store_updated"))
    assert len(audits) == 1
    assert "山野" not in str(audits[0].details)
    assert "1234" not in str(audits[0].details)


def test_staff_cannot_open_or_write(tmp_path: Path) -> None:
    """普通员工既看不到也改不了小程序首页。"""
    client, _, _ = _app(tmp_path, EmployeeRole.STAFF)
    assert client.get(PAGE).status_code == 403
    assert client.post(f"{PAGE}/store", data={"csrf_token": "x", **STORE}).status_code == 403
    assert client.get("/api/miniprogram/home").json() == {"store": None, "banners": []}
