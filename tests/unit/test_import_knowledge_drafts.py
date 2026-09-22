"""知识草稿一次性导入：全部停用写入、可重复运行、冲突整批拒绝。"""

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AuditLog, Base, Employee, KnowledgeEntry
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.tools.import_knowledge_drafts import (
    DraftError,
    ImportConflictError,
    apply_import,
    parse_drafts,
    plan_import,
    summarize,
)


def _draft(index: int, **overrides: object) -> dict[str, object]:
    """构造一条合成草稿，不含任何真实民宿事实。"""
    item: dict[str, object] = {
        "category": "测试分类",
        "question_zh": f"合成问题{index}？",
        "answer_zh": f"合成答案{index}。",
        "question_en": f"Synthetic question {index}?",
        "answer_en": f"Synthetic answer {index}.",
        "keywords": [f"关键词{index}"],
        "is_enabled": False,
    }
    item.update(overrides)
    return item


def _raw(items: list[dict[str, object]]) -> bytes:
    """把草稿列表编码成标准输入会收到的字节。"""
    return json.dumps(items, ensure_ascii=False).encode("utf-8")


async def _database(*, role: EmployeeRole = EmployeeRole.ADMIN, active: bool = True):
    """建内存库并放一名员工，返回会话工厂与员工编号。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        employee = Employee(
            wecom_userid="import-admin", name="管理员", role=role, is_active=active
        )
        session.add(employee)
        await session.commit()
        return factory, employee.id


async def _count(factory, model) -> int:
    """统计某张表的行数。"""
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def test_parse_accepts_well_formed_disabled_drafts() -> None:
    """七个字段齐全、全部停用的草稿可以解析。"""
    drafts = parse_drafts(_raw([_draft(1), _draft(2)]))

    assert [item.question_zh for item in drafts] == ["合成问题1？", "合成问题2？"]


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        ([_draft(1, is_enabled=True)], "启用"),
        ([{k: v for k, v in _draft(1).items() if k != "answer_en"}], "字段"),
        ([_draft(1, extra="x")], "字段"),
        ([_draft(1, answer_zh="  ")], "为空"),
        ([_draft(1, keywords="不是列表")], "关键词"),
        ([_draft(1, category="类" * 65)], "分类"),
        ([_draft(1), _draft(1)], "重复"),
    ],
    ids=["enabled", "missing-field", "unknown-field", "blank", "keywords", "category", "dup"],
)
def test_parse_rejects_invalid_drafts(items: list[dict[str, object]], reason: str) -> None:
    """任何不合规的记录都整批拒绝，错误信息只指出序号与原因，不带正文。"""
    with pytest.raises(DraftError) as caught:
        parse_drafts(_raw(items))

    assert reason in str(caught.value)
    assert "合成答案" not in str(caught.value)


def test_summary_counts_placeholders_without_content() -> None:
    """摘要只给数量、分类与待填写数，不打印任何正文。"""
    drafts = parse_drafts(_raw([_draft(1, answer_zh="地址：待填写。"), _draft(2)]))

    summary = summarize(drafts)

    assert summary.count == 2
    assert summary.placeholders == 1
    assert summary.categories == {"测试分类": 2}
    assert len(summary.input_digest) == 64


@pytest.mark.asyncio
async def test_preview_does_not_write() -> None:
    """默认预览只读：给出新建数，数据库不变。"""
    factory, _ = await _database()
    drafts = parse_drafts(_raw([_draft(1), _draft(2)]))

    async with factory() as session:
        plan = await plan_import(session, drafts)

    assert len(plan.new) == 2
    assert plan.skipped == 0
    assert plan.conflicts == []
    assert await _count(factory, KnowledgeEntry) == 0


@pytest.mark.asyncio
async def test_apply_writes_every_draft_disabled_with_audit() -> None:
    """写入后全部停用、记录管理员与最小审计，客人检索读不到。"""
    factory, admin_id = await _database()
    drafts = parse_drafts(_raw([_draft(1), _draft(2), _draft(3)]))

    result = await apply_import(factory, drafts, admin_id=admin_id)

    assert len(result.created_ids) == 3
    async with factory() as session:
        entries = list(await session.scalars(select(KnowledgeEntry)))
        audits = list(await session.scalars(select(AuditLog)))
        active = await SQLAlchemyKnowledgeRepository(session).list_active()
    assert all(entry.is_enabled is False for entry in entries)
    assert all(entry.updated_by == admin_id for entry in entries)
    assert active == []
    assert sorted(int(item.target_id) for item in audits) == sorted(result.created_ids)
    assert {item.action for item in audits} == {"knowledge.import_draft"}
    # 审计不复制正文。
    assert all("合成" not in json.dumps(item.details, ensure_ascii=False) for item in audits)


@pytest.mark.asyncio
async def test_rerun_skips_identical_entries() -> None:
    """同一份草稿重复导入不新增。"""
    factory, admin_id = await _database()
    drafts = parse_drafts(_raw([_draft(1), _draft(2)]))

    await apply_import(factory, drafts, admin_id=admin_id)
    second = await apply_import(factory, drafts, admin_id=admin_id)

    assert second.created_ids == []
    assert second.skipped == 2
    assert await _count(factory, KnowledgeEntry) == 2


@pytest.mark.asyncio
async def test_edited_entry_is_a_conflict_and_nothing_is_written() -> None:
    """管理员改过的条目再导入视为冲突：整批不写，不覆盖人工修改。"""
    factory, admin_id = await _database()
    await apply_import(factory, parse_drafts(_raw([_draft(1)])), admin_id=admin_id)
    async with factory() as session:
        entry = await session.scalar(select(KnowledgeEntry))
        entry.answer_zh = "管理员审核后改写的答案。"
        entry.is_enabled = True
        await session.commit()

    with pytest.raises(ImportConflictError):
        await apply_import(
            factory, parse_drafts(_raw([_draft(1), _draft(2)])), admin_id=admin_id
        )

    async with factory() as session:
        entries = list(await session.scalars(select(KnowledgeEntry)))
    assert len(entries) == 1
    assert entries[0].answer_zh == "管理员审核后改写的答案。"
    assert entries[0].is_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "active"),
    [(EmployeeRole.STAFF, True), (EmployeeRole.ADMIN, False)],
    ids=["staff", "inactive-admin"],
)
async def test_only_an_active_admin_can_import(role: EmployeeRole, active: bool) -> None:
    """操作人必须是在职管理员，不能伪造或借用普通员工身份。"""
    factory, employee_id = await _database(role=role, active=active)

    with pytest.raises(DraftError):
        await apply_import(factory, parse_drafts(_raw([_draft(1)])), admin_id=employee_id)

    assert await _count(factory, KnowledgeEntry) == 0


@pytest.mark.asyncio
async def test_unknown_admin_is_rejected() -> None:
    """不存在的员工编号直接拒绝。"""
    factory, _ = await _database()

    with pytest.raises(DraftError):
        await apply_import(factory, parse_drafts(_raw([_draft(1)])), admin_id=9999)

    assert await _count(factory, KnowledgeEntry) == 0


@pytest.mark.asyncio
async def test_failure_mid_batch_rolls_back_everything(monkeypatch) -> None:
    """写入途中出错时整批回滚，不留下部分导入结果。"""
    from homestay_bot.tools import import_knowledge_drafts as tool

    factory, admin_id = await _database()
    calls = {"n": 0}
    original = tool._audit

    def failing_audit(session, admin, entry_id):
        """第二条审计时模拟写入失败。"""
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟写入失败")
        return original(session, admin, entry_id)

    monkeypatch.setattr(tool, "_audit", failing_audit)

    with pytest.raises(RuntimeError):
        await apply_import(
            factory, parse_drafts(_raw([_draft(1), _draft(2), _draft(3)])), admin_id=admin_id
        )

    assert await _count(factory, KnowledgeEntry) == 0
    assert await _count(factory, AuditLog) == 0


@pytest.mark.asyncio
async def test_command_line_previews_then_applies_without_printing_content(
    tmp_path, monkeypatch, capsys
) -> None:
    """命令行入口：默认只预览不写入，--apply 才写入；输出不含正文与连接串。"""
    import io
    import sys

    from cryptography.fernet import Fernet
    from sqlalchemy.ext.asyncio import create_async_engine

    from homestay_bot.tools import import_knowledge_drafts as tool

    database = tmp_path / "import.db"
    url = f"sqlite+aiosqlite:///{database}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        admin = Employee(wecom_userid="cli-admin", name="管理员", role=EmployeeRole.ADMIN)
        session.add(admin)
        await session.commit()
        admin_id = admin.id
    await engine.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    raw = _raw([_draft(1), _draft(2)])

    def run(*args: str) -> int:
        """模拟从标准输入读入草稿并执行一次命令。"""
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
        return tool.main([*args])

    assert await asyncio_to_thread(run, "--admin-id", str(admin_id)) == 0
    preview = capsys.readouterr().out
    assert "新建 2，跳过 0，冲突 0" in preview
    assert "未写入" in preview

    assert await asyncio_to_thread(run, "--admin-id", str(admin_id), "--apply") == 0
    applied = capsys.readouterr().out
    assert "已写入：新建 2" in applied
    for output in (preview, applied):
        assert "合成答案" not in output
        assert "合成问题" not in output
        assert str(database) not in output

    engine = create_async_engine(url)
    async with async_sessionmaker(engine)() as session:
        entries = list(await session.scalars(select(KnowledgeEntry)))
    await engine.dispose()
    assert len(entries) == 2
    assert all(entry.is_enabled is False for entry in entries)


async def asyncio_to_thread(function, *args):
    """命令行入口内部会调用 asyncio.run，需在独立线程里执行。"""
    import asyncio

    return await asyncio.to_thread(function, *args)
