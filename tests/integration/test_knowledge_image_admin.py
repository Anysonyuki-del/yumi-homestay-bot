"""后台知识配图与房源欢迎图片（Spec 2026-09-29 G1、G3）：真实会话服务、SQLite 与私有
存储，校验格式、大小、数量、权限、顺序调整和文件清理登记。只用合成数据。
"""

import asyncio
import io
import re

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from homestay_bot.application import SessionKnowledgeAdminService, SessionPropertyAdminService
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    Job,
    KnowledgeEntry,
    KnowledgeImage,
    PropertyProfile,
)
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.sensitive_data import SensitiveDataCipher
from tests.integration.test_knowledge_routes import build_client

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
ADMIN = Employee(id=1, wecom_userid="synthetic-admin", name="合成管理员",
                 role=EmployeeRole.ADMIN, is_active=True)


def _world(tmp_path):
    """建库（每次连接新开，便于 TestClient 自己的事件循环使用）并放入一条知识和一个房间。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}", poolclass=NullPool
    )

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            session.add_all([
                Employee(id=1, wecom_userid="synthetic-admin", name="合成管理员",
                         role=EmployeeRole.ADMIN, is_active=True),
                PropertyProfile(id=1, title="合成201房"),
            ])
            await session.flush()
            session.add(KnowledgeEntry(
                id=1, scope="property", property_id=1, category="停车",
                question_zh="开车停哪里", answer_zh="地下一层。", question_en="Parking?",
                answer_en="B1.", keywords=["停车"],
            ))
            await session.commit()

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory, PrivateFileStorage(tmp_path / "private")


def _query(factory, statement):
    """在独立事件循环里读一次数据库。"""

    async def run():
        async with factory() as session:
            return list(await session.scalars(statement))

    return asyncio.run(run())


def _files(tmp_path) -> list[str]:
    """私有目录里的图片文件。"""
    return sorted(path.name for path in (tmp_path / "private").iterdir())


def _upload(client, token: str, content: bytes, content_type: str = "image/png"):
    """以一次性令牌上传一张配图。"""
    return client.post(
        "/employee/knowledge/1/images/upload",
        data={"csrf_token": token},
        files={"image": ("photo", content, content_type)},
        follow_redirects=False,
    )


def _token(client) -> str:
    """打开详情页拿一个新令牌。"""
    page = client.get("/employee/knowledge/1")
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def test_admin_uploads_up_to_three_images_and_bad_files_leave_nothing_behind(tmp_path) -> None:
    """管理员可传 3 张 JPG/PNG；webp、超 2MB、第 4 张都被拒，且不留下孤儿文件。"""
    engine, factory, storage = _world(tmp_path)
    client, _ = build_client(EmployeeRole.ADMIN)
    client.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)

    for _ in range(3):
        assert _upload(client, _token(client), PNG).status_code == 303
    assert len(_files(tmp_path)) == 3

    webp = _upload(client, _token(client), WEBP, "image/webp")
    too_big = _upload(
        client, _token(client), PNG[:-12] + b"\x00" * (2 * 1024 * 1024) + PNG[-12:]
    )
    fourth = _upload(client, _token(client), PNG)

    assert webp.status_code == 400 and "JPG 或 PNG" in webp.json()["detail"]
    assert too_big.status_code == 400
    assert fourth.status_code == 400 and "最多 3 张" in fourth.json()["detail"]
    assert len(_files(tmp_path)) == 3
    detail = client.get("/employee/knowledge/1")
    assert detail.text.count('src="/employee/knowledge/1/images/') == 3
    # 满 3 张后不再显示上传表单。
    assert 'action="/employee/knowledge/1/images/upload"' not in detail.text
    audits = _query(
        factory, select(AuditLog.details).where(AuditLog.action == "knowledge.image_add")
    )
    assert all(set(details) == {"entry_id", "image_id"} for details in audits)
    asyncio.run(engine.dispose())


def test_staff_can_view_but_not_change_images(tmp_path) -> None:
    """普通员工能预览配图，但上传、删除、排序都被拒绝。"""
    engine, factory, storage = _world(tmp_path)
    admin, _ = build_client(EmployeeRole.ADMIN)
    admin.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)
    _upload(admin, _token(admin), PNG)
    staff, _ = build_client(EmployeeRole.STAFF)
    staff.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)

    image = staff.get("/employee/knowledge/1/images/1")
    upload = _upload(staff, "any", PNG)
    delete = staff.post("/employee/knowledge/1/images/1/delete", data={"csrf_token": "any"})
    move = staff.post(
        "/employee/knowledge/1/images/1/move", data={"csrf_token": "any", "direction": "up"}
    )

    assert image.status_code == 200 and image.content == PNG
    assert image.headers["cache-control"] == "no-store"
    assert {upload.status_code, delete.status_code, move.status_code} == {403}
    # 配图只能按所属条目取，换个条目编号取不到。
    assert staff.get("/employee/knowledge/2/images/1").status_code == 404
    asyncio.run(engine.dispose())


def test_reorder_and_delete_register_file_cleanup(tmp_path) -> None:
    """后移交换发送顺序；删除后记录消失，文件清理与删除同一事务登记。"""
    engine, factory, storage = _world(tmp_path)
    client, _ = build_client(EmployeeRole.ADMIN)
    client.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)
    _upload(client, _token(client), PNG)
    _upload(client, _token(client), PNG)
    first, second = _query(factory, select(KnowledgeImage).order_by(KnowledgeImage.sort_order))

    moved = client.post(
        f"/employee/knowledge/1/images/{first.id}/move",
        data={"csrf_token": _token(client), "direction": "down"},
        follow_redirects=False,
    )
    ordered = _query(factory, select(KnowledgeImage).order_by(KnowledgeImage.sort_order))
    order = [image.id for image in ordered]
    deleted = client.post(
        f"/employee/knowledge/1/images/{first.id}/delete",
        data={"csrf_token": _token(client)},
        follow_redirects=False,
    )

    assert moved.status_code == 303 and moved.headers["location"].endswith("#knowledge-images")
    assert order == [second.id, first.id]
    assert deleted.status_code == 303
    assert [i.id for i in _query(factory, select(KnowledgeImage))] == [second.id]
    cleanup = _query(factory, select(Job).where(Job.job_type == "task_attachment_cleanup"))
    assert [job.payload["file_ids"] for job in cleanup] == [[first.file_id]]
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_welcome_image_replace_and_clear_clean_up_the_old_file(tmp_path) -> None:
    """替换欢迎图片时旧文件登记清理；清除后为空；webp 被拒且不留文件。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            Employee(id=1, wecom_userid="synthetic-admin", name="合成管理员",
                     role=EmployeeRole.ADMIN, is_active=True),
            PropertyProfile(id=1, title="合成201房"),
        ])
        await session.commit()
    storage = PrivateFileStorage(tmp_path / "private")
    service = SessionPropertyAdminService(
        factory, SensitiveDataCipher(Fernet.generate_key().decode("ascii")), storage, 1 << 22
    )

    await service.replace_welcome_image(1, ADMIN, io.BytesIO(PNG), "image/png")
    async with factory() as session:
        first = (await session.get(PropertyProfile, 1)).welcome_image_file_id
    assert (await service.welcome_image_for(1, ADMIN)).path.read_bytes() == PNG

    await service.replace_welcome_image(1, ADMIN, io.BytesIO(PNG), "image/png")
    with pytest.raises(ValueError):
        await service.replace_welcome_image(1, ADMIN, io.BytesIO(WEBP), "image/webp")
    await service.replace_welcome_image(1, ADMIN, None)

    async with factory() as session:
        profile = await session.get(PropertyProfile, 1)
        cleanups = list(await session.scalars(
            select(Job).where(Job.job_type == "task_attachment_cleanup").order_by(Job.id)
        ))
        audits = list(await session.scalars(
            select(AuditLog.action).where(AuditLog.target_type == "property_profile")
        ))
    assert profile.welcome_image_file_id is None
    assert cleanups[0].payload["file_ids"] == [first]
    assert len(cleanups) == 2
    assert audits.count("property_welcome_image_set") == 2
    assert audits.count("property_welcome_image_cleared") == 1
    # 两张上传过的 PNG 仍在目录里等 worker 清理；被拒的 webp 没留下文件。
    assert len(list((tmp_path / "private").iterdir())) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_service_passes_the_room_filter(tmp_path) -> None:
    """回归：生产知识列表服务接受并转交房间筛选（1.48.0 起路由会传 room）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'room.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            PropertyProfile(id=1, title="合成甲房"), PropertyProfile(id=2, title="合成乙房")
        ])
        await session.flush()
        session.add_all([
            KnowledgeEntry(scope="property", property_id=pid, category="地址",
                           question_zh="地址", answer_zh=f"房{pid}", question_en="Address",
                           answer_en="-", keywords=[])
            for pid in (1, 2)
        ])
        await session.commit()

    listed = await SessionKnowledgeAdminService(factory).list_all(offset=0, limit=10, room="2")

    assert [entry.answer_zh for entry in listed] == ["房2"]
    await engine.dispose()


def _seed_derived_rows(factory) -> None:
    """给条目 1 补一行向量和一个已转为该知识的候选。"""
    from datetime import UTC, datetime

    from homestay_bot.domain.enums import KnowledgeCandidateStatus
    from homestay_bot.domain.models import KnowledgeCandidate, KnowledgeEmbedding

    async def run() -> None:
        async with factory() as session:
            session.add(
                KnowledgeEmbedding(
                    entry_id=1, language="zh", model="synthetic", content_hash="h",
                    dimensions=2, vector=[0.1, 0.2],
                )
            )
            session.add(
                KnowledgeCandidate(
                    id=5, canonical_key="开车停哪里", canonical_question="开车停哪里",
                    category="停车", status=KnowledgeCandidateStatus.CONVERTED,
                    knowledge_entry_id=1, total_occurrences=9, last_threshold_total=9,
                    last_reminded_total=9, last_reminded_at=datetime.now(UTC),
                    draft_generation=4,
                )
            )
            await session.commit()

    asyncio.run(run())


def test_delete_entry_removes_everything_and_reopens_its_candidate(tmp_path) -> None:
    """AC08/D3/D4-A：删除一并清掉配图、向量；候选改回待处理并从零计数；文件提交后清理。"""
    from homestay_bot.application import _build_attachment_cleanup_handler
    from homestay_bot.domain.enums import KnowledgeCandidateStatus
    from homestay_bot.domain.models import KnowledgeCandidate, KnowledgeEmbedding
    from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository

    engine, factory, storage = _world(tmp_path)
    client, _ = build_client(EmployeeRole.ADMIN)
    client.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)
    for _ in range(2):
        _upload(client, _token(client), PNG)
    _seed_derived_rows(factory)
    files_before = _files(tmp_path)

    response = client.post(
        "/employee/knowledge/1/delete",
        data={"csrf_token": _token(client), "return_to": "/employee/knowledge"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _query(factory, select(KnowledgeEntry)) == []
    assert _query(factory, select(KnowledgeImage)) == []
    assert _query(factory, select(KnowledgeEmbedding)) == []
    [candidate] = _query(factory, select(KnowledgeCandidate))
    assert candidate.status is KnowledgeCandidateStatus.OPEN
    assert candidate.knowledge_entry_id is None
    assert (candidate.total_occurrences, candidate.last_threshold_total) == (0, 0)
    assert candidate.last_reminded_at is None
    assert candidate.draft_generation == 5  # 只增不减，旧代次任务作废
    [job] = _query(
        factory, select(Job).where(Job.dedupe_key.startswith("knowledge-entry-cleanup:1:"))
    )
    assert sorted(job.payload["file_ids"]) == files_before
    [audit] = _query(factory, select(AuditLog).where(AuditLog.action == "knowledge.delete"))
    assert audit.details == {"entry_id": 1, "image_count": 2}
    # 文件要等清理任务执行后才删：提交前删文件，事务一旦失败就无法恢复。
    assert _files(tmp_path) == files_before
    asyncio.run(_build_attachment_cleanup_handler(storage)(job.payload))
    assert _files(tmp_path) == []

    async def ask_again() -> bool:
        async with factory() as session:
            repository = SQLAlchemyFaqCandidateRepository(session)
            same = await repository.get_or_create(
                canonical_question="开车停哪里", category="停车"
            )
            from datetime import UTC, datetime

            return await repository.add_occurrence(
                same.id, source_message_id="m-1", occurred_at=datetime.now(UTC), example=None
            )

    # 候选重新开放后，同一问题再次出现会被计数，而不是被 CONVERTED 静默吞掉。
    assert asyncio.run(ask_again()) is True
    asyncio.run(engine.dispose())


def test_failed_delete_rolls_back_and_keeps_files(tmp_path, monkeypatch) -> None:
    """清理任务登记失败时整笔回滚：条目、配图、候选都不变，文件还在。"""
    from homestay_bot.domain.enums import KnowledgeCandidateStatus
    from homestay_bot.domain.models import KnowledgeCandidate
    from homestay_bot.repositories.jobs import SQLAlchemyJobRepository

    engine, factory, storage = _world(tmp_path)
    client, _ = build_client(EmployeeRole.ADMIN)
    client.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)
    _upload(client, _token(client), PNG)
    _seed_derived_rows(factory)
    token = _token(client)

    async def broken_enqueue(self, *args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(SQLAlchemyJobRepository, "enqueue", broken_enqueue)
    with pytest.raises(RuntimeError):
        client.post("/employee/knowledge/1/delete", data={"csrf_token": token})

    assert len(_query(factory, select(KnowledgeEntry))) == 1
    assert len(_query(factory, select(KnowledgeImage))) == 1
    [candidate] = _query(factory, select(KnowledgeCandidate))
    assert candidate.status is KnowledgeCandidateStatus.CONVERTED
    assert len(_files(tmp_path)) == 1
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("old_job_status", ["pending", "completed"])
def test_reused_entry_number_still_registers_its_own_cleanup(tmp_path, old_job_status) -> None:
    """M4：SQLite 复用被删的最大编号；第二次删除要为新文件另登记清理，两组文件都能清掉。"""
    from homestay_bot.application import _build_attachment_cleanup_handler
    from homestay_bot.domain.enums import JobStatus

    engine, factory, storage = _world(tmp_path)
    client, _ = build_client(EmployeeRole.ADMIN)
    client.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)

    def create_entry() -> int:
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', client.get("/employee/knowledge").text
        ).group(1)
        client.post("/employee/knowledge", data={
            "csrf_token": token, "category": "早餐", "question_zh": "早餐？", "answer_zh": "7点",
            "question_en": "Breakfast?", "answer_en": "At 7", "scope": "global",
        })
        return max(_query(factory, select(KnowledgeEntry.id)))

    # 先删掉夹具自带的条目 1，让新建条目拿到可复用的最大编号。
    client.post("/employee/knowledge/1/delete", data={"csrf_token": _token(client)})
    first = create_entry()
    _upload_to(client, first, PNG)
    client.post(f"/employee/knowledge/{first}/delete", data={"csrf_token": _token(client)})
    first_files = _files(tmp_path)

    async def settle_old_job() -> None:
        async with factory() as session:
            for job in await session.scalars(select(Job)):
                job.status = JobStatus(old_job_status)
            await session.commit()

    asyncio.run(settle_old_job())
    second = create_entry()
    assert second == first  # SQLite 复用了编号，正是这个反例的前提
    _upload_to(client, second, PNG)
    client.post(f"/employee/knowledge/{second}/delete", data={"csrf_token": _token(client)})

    jobs = _query(factory, select(Job).where(Job.dedupe_key.startswith(
        f"knowledge-entry-cleanup:{first}:")))
    assert len(jobs) == 2
    handler = _build_attachment_cleanup_handler(storage)
    for job in jobs:
        asyncio.run(handler(job.payload))
    assert _files(tmp_path) == []
    assert first_files
    asyncio.run(engine.dispose())


def _upload_to(client, entry_id: int, content: bytes):
    """给指定条目上传一张配图。"""
    page = client.get(f"/employee/knowledge/{entry_id}")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client.post(
        f"/employee/knowledge/{entry_id}/images/upload",
        data={"csrf_token": token},
        files={"image": ("photo", content, "image/png")},
        follow_redirects=False,
    )


def test_list_delete_confirm_states_how_many_images_go_with_it(tmp_path) -> None:
    """列表页删除确认框写明会一并删除几张配图，与详情页一致。"""
    engine, factory, storage = _world(tmp_path)
    client, _ = build_client(EmployeeRole.ADMIN)
    client.app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)
    for _ in range(2):
        _upload(client, _token(client), PNG)

    listing = client.get("/employee/knowledge").text

    assert "确定永久删除知识 #1「开车停哪里」及其 2 张配图吗？" in listing
    asyncio.run(engine.dispose())
