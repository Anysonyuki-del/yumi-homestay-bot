"""知识库重构（2026-11-25 Spec §5.2）：英文生效不变式、多房间新建、快捷定界与跨房复制。

全部走真实 `SessionKnowledgeAdminService`、临时 SQLite 与私有文件目录，只用合成数据；
不调用任何外部服务。
"""

import asyncio
import re
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from homestay_bot.application import SessionKnowledgeAdminService
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    KnowledgeEntry,
    KnowledgeImage,
    PropertyProfile,
)
from homestay_bot.routes import knowledge as knowledge_routes
from homestay_bot.routes.knowledge import (
    MAX_COPY_IMAGE_FILES,
    KnowledgeAdminService,
    KnowledgeFormError,
)
from homestay_bot.routes.page_errors import handle_operation_refused
from homestay_bot.services.private_file_storage import PrivateFileStorage
from tests.integration.test_knowledge_routes import build_client

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_HTML = {"accept": "text/html"}
_QA = {
    "category": "早餐",
    "question_zh": "早餐几点？",
    "answer_zh": "七点到九点。",
    "question_en": "Breakfast time?",
    "answer_en": "7 to 9 AM.",
}


class World:
    """一套合成数据：在用房 201/202、停用房 203，房间知识 #1（两张配图）与全店知识 #2。"""

    def __init__(self, tmp_path) -> None:
        self.db_path = tmp_path / "kb.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}", poolclass=NullPool
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.storage = PrivateFileStorage(tmp_path / "private")
        self.private = tmp_path / "private"
        self.run(self._seed())
        self.service = SessionKnowledgeAdminService(self.factory, self.storage)

    def run(self, coroutine):
        """在独立事件循环里执行一次数据库协程。"""
        return asyncio.run(coroutine)

    async def _seed(self) -> None:
        """建表并放入合成房间、知识和真实存在的配图文件。"""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        files = [
            (await self.storage.save_image(_Stream(PNG), "image/png", 10_000)).file_id
            for _ in range(2)
        ]
        async with self.factory() as session:
            session.add_all([
                Employee(id=1, wecom_userid="synthetic-admin", name="合成管理员",
                         role=EmployeeRole.ADMIN, is_active=True),
                PropertyProfile(id=1, room_number="201", title="合成201房"),
                PropertyProfile(id=2, room_number="202", title="合成202房"),
                PropertyProfile(id=3, room_number="203", title="合成停用房", is_active=False),
            ])
            await session.flush()
            session.add_all([
                KnowledgeEntry(id=1, scope="property", property_id=1, **_QA,
                               keywords=["早餐"], trigger_any=["几点"]),
                KnowledgeEntry(id=2, scope="global", category="停车", question_zh="停哪里？",
                               answer_zh="地下一层。", question_en="Parking?", answer_en="B1.",
                               keywords=["停车"], show_in_miniprogram=True),
            ])
            await session.flush()
            session.add_all([
                KnowledgeImage(knowledge_entry_id=1, file_id=file_id, content_type="image/png",
                               size=len(PNG), sort_order=order, created_by=1)
                for order, file_id in enumerate(files, start=1)
            ])
            await session.commit()
        self.source_files = files

    def entries(self) -> list[KnowledgeEntry]:
        """按编号读取全部知识。"""

        async def read():
            async with self.factory() as session:
                return list(
                    await session.scalars(select(KnowledgeEntry).order_by(KnowledgeEntry.id))
                )

        return self.run(read())

    def images(self, entry_id: int) -> list[KnowledgeImage]:
        """读取某条知识的配图记录。"""

        async def read():
            async with self.factory() as session:
                return list(await session.scalars(
                    select(KnowledgeImage).where(KnowledgeImage.knowledge_entry_id == entry_id)
                    .order_by(KnowledgeImage.sort_order)
                ))

        return self.run(read())

    def files(self) -> set[str]:
        """私有目录里的文件名。"""
        return {path.name for path in self.private.iterdir()}

    def client(self):
        """真实服务装配的管理员页面，挂上与生产相同的业务拒绝处理器。"""
        client, _ = build_client(EmployeeRole.ADMIN)
        client.app.state.knowledge_admin_service = self.service
        client.app.add_exception_handler(OperationRefused, handle_operation_refused)
        # build_client 已登录过一次，中间件栈已建好；清空后下次请求按新处理器重建。
        client.app.middleware_stack = None
        return client


class _Stream:
    """最小可读流，供 save_image 读取合成图片。"""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def read(self, size: int = -1) -> bytes:
        """一次性返回全部内容。"""
        return self._content


@pytest.fixture
def world(tmp_path):
    """每个用例一套独立的库与私有目录。"""
    instance = World(tmp_path)
    yield instance
    asyncio.run(instance.engine.dispose())


def _token(client, path: str = "/employee/knowledge/new") -> str:
    """打开页面取一次性令牌。"""
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match is not None
    return match.group(1)


# ---- 英文生效不变式（Spec §5.2 四条必测） ----


def test_editing_an_enabled_entry_to_empty_english_is_refused_and_keeps_input(world) -> None:
    """启用中的审核知识清空英文 → 422 原地重绘并保留输入，库里英文不变。"""
    client = world.client()
    response = client.post(
        "/employee/knowledge/1/edit",
        data={**_QA, "answer_zh": "改过但没保存", "question_en": "", "answer_en": "",
              "scope": "property", "property_id": "1", "csrf_token": _token(client)},
        headers=_HTML,
    )

    assert response.status_code == 422
    assert "启用中的审核知识必须填写英文问题和英文答案" in response.text
    assert "改过但没保存" in response.text
    entry = world.entries()[0]
    assert (entry.question_en, entry.answer_en, entry.answer_zh) == (
        "Breakfast time?", "7 to 9 AM.", "七点到九点。"
    )


def test_creating_without_english_saves_disabled_drafts_for_single_and_multi_room(world) -> None:
    """新建（单条与多房）留空英文 → 成功保存为停用草稿，并提示补全英文。"""
    client = world.client()
    single = client.post(
        "/employee/knowledge",
        data={**_QA, "question_en": "", "answer_en": "", "scope": "global",
              "csrf_token": _token(client)},
        headers=_HTML,
    )
    assert single.status_code == 200  # 跟随重定向到全店通用分区
    assert "已保存为停用草稿，补全英文后才能启用" in single.text

    multi = client.post(
        "/employee/knowledge",
        data={**_QA, "question_en": " ", "answer_en": "", "scope": "property",
              "property_ids": ["1", "2"], "csrf_token": _token(client)},
        headers=_HTML,
        follow_redirects=False,
    )
    assert multi.status_code == 303
    assert multi.headers["location"] == "/employee/knowledge?section=rooms"

    created = world.entries()[2:]
    assert [(entry.scope, entry.property_id, entry.is_enabled) for entry in created] == [
        ("global", None, False), ("property", 1, False), ("property", 2, False),
    ]
    assert all(entry.question_en == "" for entry in created[:1])


def test_disabled_entry_can_be_enabled_only_after_english_is_filled(world) -> None:
    """停用且缺英文的条目启用被拒；补全英文后可以启用。"""
    client = world.client()
    client.post(
        "/employee/knowledge",
        data={**_QA, "question_en": "", "answer_en": "", "scope": "global",
              "csrf_token": _token(client)},
    )
    draft_id = world.entries()[-1].id

    refused = client.post(f"/employee/knowledge/{draft_id}/enable",
                          data={"csrf_token": _token(client)})
    assert refused.status_code == 422
    assert world.entries()[-1].is_enabled is False

    client.post(
        f"/employee/knowledge/{draft_id}/edit",
        data={**_QA, "scope": "global", "csrf_token": _token(client)},
    )
    enabled = client.post(
        f"/employee/knowledge/{draft_id}/enable",
        data={"csrf_token": _token(client),
              "return_to": f"/employee/knowledge/{draft_id}#entry-status"},
        follow_redirects=False,
    )
    assert enabled.status_code == 303
    # 详情页来源自带锚点，不得被改写成列表锚点。
    assert enabled.headers["location"] == f"/employee/knowledge/{draft_id}#entry-status"
    assert world.entries()[-1].is_enabled is True


@pytest.mark.parametrize(
    ("question_en", "answer_zh", "message"),
    [
        ("", "七点到九点。", "英文"),
        ("Breakfast time?", "【待管理员确认】", "待管理员确认"),
    ],
)
def test_quick_scoping_refuses_enabled_entries_missing_english_or_with_placeholders(
    world, question_en, answer_zh, message
) -> None:
    """快捷定界一条已启用但缺英文或含占位符的待审核条目 → 被拒，保持待审核。"""

    async def seed():
        async with world.factory() as session:
            session.add(KnowledgeEntry(
                id=10, scope="unreviewed", category="早餐", question_zh="早餐？",
                answer_zh=answer_zh, question_en=question_en, answer_en="7 to 9 AM.",
                keywords=[], is_enabled=True,
            ))
            await session.commit()

    world.run(seed())
    client = world.client()
    response = client.post(
        "/employee/knowledge/10/scope",
        data={"scope": "global", "csrf_token": _token(client)},
    )

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert world.entries()[-1].scope == "unreviewed"


def test_quick_scoping_only_accepts_unreviewed_entries_and_keeps_miniprogram_flag(world) -> None:
    """非待审核条目不能快捷定界；待审核条目定界成功后保留小程序勾选并记审计。"""

    async def seed():
        async with world.factory() as session:
            session.add(KnowledgeEntry(
                id=10, scope="unreviewed", keywords=[], is_enabled=True,
                show_in_miniprogram=True, **_QA,
            ))
            await session.commit()

    world.run(seed())
    client = world.client()
    refused = client.post("/employee/knowledge/2/scope",
                          data={"scope": "public", "csrf_token": _token(client)})
    assert refused.status_code == 422
    assert "仅范围待审核的条目" in refused.json()["detail"]

    done = client.post(
        "/employee/knowledge/10/scope",
        data={"scope": "global", "csrf_token": _token(client)},
        follow_redirects=False,
    )
    assert done.status_code == 303
    entry = world.entries()[-1]
    assert (entry.scope, entry.show_in_miniprogram) == ("global", True)


def test_candidate_conversion_shares_the_english_invariant(world) -> None:
    """候选转换直接建启用知识：审核范围缺英文被拒，范围待审核允许留空。"""

    async def candidate():
        from homestay_bot.domain.enums import KnowledgeCandidateStatus
        from homestay_bot.domain.models import KnowledgeCandidate

        async with world.factory() as session:
            session.add(KnowledgeCandidate(
                id=5, canonical_key="早餐", canonical_question="早餐？", category="早餐",
                status=KnowledgeCandidateStatus.OPEN, total_occurrences=3,
            ))
            await session.commit()
        async with world.factory() as session:
            with pytest.raises(KnowledgeFormError, match="英文"):
                await KnowledgeAdminService(session).convert_candidate(
                    5, 1, scope="global", property_id=None, **{**_QA, "answer_en": ""},
                    keywords=[],
                )
        async with world.factory() as session:
            # 范围待审核的转换不参与回答，英文可以留空。
            entry = await KnowledgeAdminService(session).convert_candidate(
                5, 1, scope="unreviewed", property_id=None, **{**_QA, "answer_en": ""},
                keywords=[],
            )
            assert entry.is_enabled is True

    world.run(candidate())


# ---- 多房间新建校验矩阵（Spec §3.2(2)） ----


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"scope": "property", "property_id": "1", "property_ids": ["2"]}, "只用一种方式"),
        ({"scope": "property", "property_ids": ["abc"]}, "房间编号无效"),
        ({"scope": "property", "property_ids": ["1", ""]}, "房间编号无效"),
        ({"scope": "property", "property_ids": ["0"]}, "房间编号无效"),
        ({"scope": "property", "property_ids": ["1", "99"]}, "编号 99"),
        ({"scope": "property", "property_ids": ["1", "3"]}, "不能为已停用房间新增知识"),
        ({"scope": "global", "property_ids": ["1"]}, "全店通用 / 店外周边不能指定房间"),
        ({"scope": "public", "property_id": "1"}, "全店通用 / 店外周边不能指定房间"),
        ({"scope": "property"}, "请至少勾选一个房间"),
    ],
)
def test_multi_room_create_rejects_every_bad_room_combination(world, form, message) -> None:
    """每种错误都是 422 原地重绘新建页、不写库，多房勾选原样保留。"""
    client = world.client()
    response = client.post(
        "/employee/knowledge",
        data={**_QA, **form, "csrf_token": _token(client)},
        headers=_HTML,
    )

    assert response.status_code == 422
    assert message in response.text
    assert 'action="/employee/knowledge"' in response.text
    assert len(world.entries()) == 2


def test_multi_room_create_dedupes_ids_and_redirects_single_room_to_that_room(world) -> None:
    """重复编号去重保序；只建一间房时回到该房间。"""
    client = world.client()
    many = client.post(
        "/employee/knowledge",
        data={**_QA, "scope": "property", "property_ids": ["2", "1", "2"],
              "csrf_token": _token(client)},
        follow_redirects=False,
    )
    single = client.post(
        "/employee/knowledge",
        data={**_QA, "scope": "property", "property_id": "2", "csrf_token": _token(client)},
        follow_redirects=False,
    )

    assert many.headers["location"] == "/employee/knowledge?section=rooms"
    assert single.headers["location"] == "/employee/knowledge?section=rooms&room=2"
    created = world.entries()[2:]
    assert [(entry.property_id, entry.is_enabled) for entry in created] == [
        (2, True), (1, True), (2, True),
    ]


def test_multi_room_create_rolls_back_the_whole_batch(world, monkeypatch) -> None:
    """第二间房写审计时出错，整批回滚：一条也不留下。"""
    original = KnowledgeAdminService._add_audit
    calls = {"count": 0}

    def flaky(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("synthetic failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeAdminService, "_add_audit", flaky)

    async def create():
        with pytest.raises(RuntimeError):
            await world.service.create_for_rooms(
                1, [1, 2], scope="property", property_id=1, keywords=[], **_QA
            )

    world.run(create())
    assert len(world.entries()) == 2


# ---- 跨房复制（Spec §3.2(4)） ----


def test_copy_makes_disabled_independent_drafts_that_survive_source_deletion(world) -> None:
    """复制结果为停用、不进小程序、独立新文件；删源条目并清理源文件后副本图片仍在。"""
    client = world.client()
    response = client.post(
        "/employee/knowledge/1/copy",
        data={"property_ids": ["2"], "copy_images": "1", "csrf_token": _token(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("#entry-copy")

    copy = world.entries()[-1]
    assert (copy.scope, copy.property_id, copy.is_enabled, copy.show_in_miniprogram) == (
        "property", 2, False, False,
    )
    assert (copy.question_zh, copy.trigger_any) == ("早餐几点？", ["几点"])
    copied = [image.file_id for image in world.images(copy.id)]
    assert len(copied) == 2 and not set(copied) & set(world.source_files)

    world.run(world.service.delete_entry(1, 1))
    for file_id in world.source_files:  # 等同附件清理任务删除源文件
        world.storage.delete(file_id)
    for file_id in copied:
        assert world.storage.open_for_read(file_id).size == len(PNG)

    async def audits():
        async with world.factory() as session:
            return list(await session.scalars(
                select(AuditLog.details).where(AuditLog.action == "knowledge.copy")
            ))

    assert world.run(audits()) == [{"entry_id": copy.id, "source_entry_id": 1, "image_count": 2}]


def test_copying_a_global_entry_reports_rooms_that_already_have_the_question(world) -> None:
    """全店知识可复制到房间；目标房已有同名（忽略空白大小写）问题时提示。"""

    async def seed():
        async with world.factory() as session:
            session.add(KnowledgeEntry(scope="property", property_id=2, category="停车",
                                       question_zh=" 停 哪里？", answer_zh="门口。",
                                       question_en="", answer_en="", keywords=[]))
            await session.commit()

    world.run(seed())
    result = world.run(world.service.copy_to_rooms(2, 1, [1, 2], copy_images=True))
    assert result.duplicate_rooms == 1
    assert len(result.entry_ids) == 2


def test_copy_refuses_too_many_image_files_before_copying_anything(world, monkeypatch) -> None:
    """目标房 × 配图数超过 36 时直接拒绝，私有目录不多一个文件。"""
    monkeypatch.setattr(knowledge_routes, "MAX_COPY_IMAGE_FILES", 1)
    before = world.files()

    with pytest.raises(KnowledgeFormError, match="最多复制 1 个图片文件"):
        world.run(world.service.copy_to_rooms(1, 1, [2], copy_images=True))
    assert world.files() == before
    assert MAX_COPY_IMAGE_FILES == 36


@pytest.mark.parametrize(
    ("targets", "message"),
    [([1], "原本所在的房间"), ([3], "已停用"), ([99], "编号 99"), ([], "至少选择")],
)
def test_copy_validates_target_rooms(world, targets, message) -> None:
    """不能复制到源房间、停用房间、不存在的房间，也不能一间都不选。"""
    with pytest.raises(KnowledgeFormError, match=message):
        world.run(world.service.copy_to_rooms(1, 1, targets, copy_images=False))
    assert len(world.entries()) == 2


def test_copy_aborts_and_cleans_up_when_source_images_change_meanwhile(world, monkeypatch) -> None:
    """拷贝文件期间源配图被删：锁内复核失败返回 409，拷出的文件全部删除、不写库。"""
    original = PrivateFileStorage.copy_image
    changed = {"done": False}

    def copy_then_change(self, file_id):
        stored = original(self, file_id)
        # 第一次拷贝后模拟另一位管理员删掉一张源配图（另开同步连接，等同并发请求）。
        if not changed["done"]:
            changed["done"] = True
            with sqlite3.connect(world.db_path) as connection:
                connection.execute(
                    "DELETE FROM knowledge_images WHERE id = (SELECT MIN(id) FROM knowledge_images)"
                )
        return stored

    monkeypatch.setattr(PrivateFileStorage, "copy_image", copy_then_change)
    before = world.files()

    with pytest.raises(KnowledgeFormError, match="源条目或配图已变化") as error:
        world.run(world.service.copy_to_rooms(1, 1, [2], copy_images=True))
    assert error.value.status_code == 409
    assert world.files() == before
    assert len(world.entries()) == 2


def test_copy_cleans_up_when_a_source_file_is_missing(world) -> None:
    """源图片文件已不在磁盘：给出可读错误，已拷出的副本删除。"""
    world.storage.delete(world.source_files[1])
    before = world.files()

    with pytest.raises(KnowledgeFormError, match="复制配图失败"):
        world.run(world.service.copy_to_rooms(1, 1, [2], copy_images=True))
    assert world.files() == before
    assert len(world.entries()) == 2


def test_copy_keeps_copied_files_when_commit_itself_fails(world, monkeypatch) -> None:
    """提交本身失败时无法确定是否已落库，宁留孤儿文件也不删副本。"""
    before = world.files()
    original = AsyncSession.commit

    async def failing_commit(self):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        world.run(world.service.copy_to_rooms(1, 1, [2], copy_images=True))
    monkeypatch.setattr(AsyncSession, "commit", original)

    assert len(world.files() - before) == 2
    assert len(world.entries()) == 2


def test_copy_route_errors_return_to_the_copy_panel(world) -> None:
    """页面请求失败时带提示回到详情页复制区；接口请求得到 422 JSON。"""
    client = world.client()
    page = client.post(
        "/employee/knowledge/1/copy",
        data={"property_ids": ["3"], "csrf_token": _token(client)},
        headers=_HTML,
        follow_redirects=False,
    )
    assert page.status_code == 303
    assert page.headers["location"].startswith("/employee/knowledge/1?return_to=")
    assert page.headers["location"].endswith("#entry-copy")
    assert "不能复制到已停用的房间" in client.get(page.headers["location"]).text

    api = client.post(
        "/employee/knowledge/1/copy",
        data={"property_ids": ["x"], "csrf_token": _token(client)},
    )
    assert api.status_code == 422


# ---- 工作区页面 ----


def test_sections_split_scopes_and_count_search_hits_across_tabs(world) -> None:
    """默认全店通用；搜索时各页签显示命中数，零命中时给出跨区链接。"""
    client = world.client()
    default = client.get("/employee/knowledge").text
    assert "停哪里？" in default and "早餐几点？" not in default

    searched = client.get("/employee/knowledge?query=早餐").text
    assert "全店通用暂无匹配" in searched
    assert "按房间管理中有 1 条匹配 → 去查看" in searched
    assert re.search(r'section=rooms[^"]*query=%E6%97%A9%E9%A4%90', searched)

    rooms = client.get("/employee/knowledge?room=1").text
    assert "早餐几点？" in rooms
    assert "201 · 合成201房 的专属知识" in rooms
    assert "+ 为【201 · 合成201房】新增知识" in rooms
    assert 'href="/employee/knowledge/new?scope=property&amp;property_id=1' in rooms
    assert "配图 2 张" in rooms

    bad = client.get("/employee/knowledge?section=nope")
    assert bad.status_code == 422


def test_new_page_presets_scope_and_room_and_is_admin_only(world) -> None:
    """新建页按链接预设归属与房间；只列在用房间；普通员工 403。"""
    client = world.client()
    page = client.get("/employee/knowledge/new?scope=property&property_id=2").text
    assert 'value="property" checked' in page
    assert 'name="property_ids" value="2" checked' in page
    assert 'name="property_ids" value="3"' not in page

    staff, _ = build_client(EmployeeRole.STAFF)
    staff.app.state.knowledge_admin_service = world.service
    assert staff.get("/employee/knowledge/new").status_code == 403


def test_detail_page_shows_notice_and_copy_targets_exclude_source_and_inactive(world) -> None:
    """详情页读取页面提示；复制目标只列其他在用房间。"""
    client = world.client()
    client.post("/employee/knowledge/1/disable",
                data={"csrf_token": _token(client),
                      "return_to": "/employee/knowledge/1#entry-status"},
                follow_redirects=False)
    detail = client.get("/employee/knowledge/1").text

    assert "已停用知识 #1" in detail
    copy_panel = detail.split('id="entry-copy"', 1)[1]
    assert 'name="property_ids" value="2"' in copy_panel
    assert 'name="property_ids" value="1"' not in copy_panel
    assert 'name="property_ids" value="3"' not in copy_panel


def test_image_counts_are_one_grouped_query(world) -> None:
    """列表角标的配图数一次查询返回，没有配图的条目不出现。"""

    async def counts():
        async with world.factory() as session:
            return await KnowledgeAdminService(session).image_counts_for_entries([1, 2])

    assert world.run(counts()) == {1: 2}


def test_detail_status_toggle_preserves_list_return_to_within_limit(world) -> None:
    """从房间列表进详情页并切换状态后，仍保留原列表返回路径；超长路径安全回退不 422。"""
    client = world.client()
    list_path = "/employee/knowledge?section=rooms&room=1"
    detail = client.get("/employee/knowledge/1", params={"return_to": list_path}).text
    match = re.search(
        r'action="/employee/knowledge/1/disable"[^>]*>'
        r'<input type="hidden" name="return_to" value="([^"]+)"',
        detail,
    )
    assert match is not None
    status_return = match.group(1).replace("&amp;", "&")
    assert len(status_return) <= 200

    res = client.post(
        "/employee/knowledge/1/disable",
        data={"csrf_token": _token(client), "return_to": status_return},
        follow_redirects=False,
    )
    assert res.status_code == 303
    after = client.get(res.headers["location"].split("#", 1)[0]).text
    assert 'href="/employee/knowledge?section=rooms&amp;room=1">返回知识列表</a>' in after

    long_return = "/employee/knowledge?section=rooms&query=" + ("a" * 155)
    long_detail = client.get("/employee/knowledge/1", params={"return_to": long_return}).text
    long_match = re.search(
        r'action="/employee/knowledge/1/enable"[^>]*>'
        r'<input type="hidden" name="return_to" value="([^"]+)"',
        long_detail,
    )
    assert long_match is not None
    long_status_return = long_match.group(1).replace("&amp;", "&")
    assert long_status_return == "/employee/knowledge/1#entry-status"
    res2 = client.post(
        "/employee/knowledge/1/enable",
        data={"csrf_token": _token(client), "return_to": long_status_return},
        follow_redirects=False,
    )
    assert res2.status_code == 303
