"""一次性导入知识草稿：全部以停用状态写入，供管理员在后台逐条审核后自行启用。

后台「新建知识」固定启用并逐条提交，用它导入未审核草稿会让草稿立刻进入客人
回复；先建后停又会留下可被检索的窗口。本工具在一个事务里直接写入停用条目。

用法（在 API 容器内执行；草稿 JSON 从标准输入读入，不在服务器落盘）：

    python -m homestay_bot.tools.import_knowledge_drafts --admin-id N < drafts.json
    python -m homestay_bot.tools.import_knowledge_drafts --admin-id N --apply < drafts.json

不带 --apply 只预览，不写数据库。输出只含数量、分类、摘要与条目编号，不打印正文、
连接串或凭据。

ponytail: 专为一批草稿的一次性导入而写，不是通用导入平台；知识表没有唯一约束，
PostgreSQL 上用事务级咨询锁保证串行，其他数据库只适合单人手动运行。
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AuditLog, Employee, KnowledgeEntry
from homestay_bot.services.knowledge_service import normalize_text

TEXT_FIELDS = ("category", "question_zh", "answer_zh", "question_en", "answer_en")
ALLOWED_FIELDS = frozenset((*TEXT_FIELDS, "keywords", "is_enabled"))
CATEGORY_MAX_CHARS = 64
AUDIT_ACTION = "knowledge.import_draft"
# 事务级咨询锁的固定编号，只用于让两次导入不能并发执行。
_ADVISORY_LOCK_KEY = 72_031_939
_PLACEHOLDER = re.compile(r"待填写|to\s+confirm", re.IGNORECASE)


class DraftError(ValueError):
    """草稿或操作人不合规；错误信息只含序号与原因，不含正文。"""


class ImportConflictError(RuntimeError):
    """已有条目与草稿身份相同但内容不同，整批拒绝写入。"""


@dataclass(frozen=True)
class DraftEntry:
    """一条经过校验的停用草稿。"""

    category: str
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str
    keywords: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, str, str]:
        """同一条知识的身份：分类与中英文问题，统一全半角、大小写和空白后比较。"""
        return (
            _canonical(self.category),
            _canonical(self.question_zh),
            _canonical(self.question_en),
        )

    def same_content(self, entry: KnowledgeEntry) -> bool:
        """判断数据库里的条目与草稿正文、关键词是否完全一致。"""
        return (
            entry.answer_zh.strip() == self.answer_zh
            and entry.answer_en.strip() == self.answer_en
            and tuple(entry.keywords or ()) == self.keywords
        )


@dataclass(frozen=True)
class DraftSummary:
    """只含数量与摘要的输入概况，可以安全打印。"""

    count: int
    categories: dict[str, int]
    placeholders: int
    input_digest: str


@dataclass(frozen=True)
class ImportPlan:
    """对照数据库现状得出的导入计划；冲突用输入序号表示。"""

    new: list[DraftEntry]
    skipped: int
    conflicts: list[int]


@dataclass(frozen=True)
class ImportResult:
    """实际写入结果。"""

    created_ids: list[int]
    skipped: int


def _canonical(value: str) -> str:
    """比较身份用的规范化文本。"""
    return " ".join(normalize_text(value).split())


def parse_drafts(raw: bytes) -> list[DraftEntry]:
    """严格校验草稿：字段、类型、非空、分类长度、全部停用、身份不重复。"""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DraftError("输入不是 UTF-8 编码的 JSON") from error
    if not isinstance(data, list) or not data:
        raise DraftError("输入必须是非空的 JSON 数组")

    drafts: list[DraftEntry] = []
    seen: dict[tuple[str, str, str], int] = {}
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict) or set(item) != ALLOWED_FIELDS:
            raise DraftError(f"第 {index} 条字段不符：必须恰好包含 {sorted(ALLOWED_FIELDS)}")
        if item["is_enabled"] is not False:
            raise DraftError(f"第 {index} 条是启用状态：草稿必须全部停用")
        values: dict[str, str] = {}
        for name in TEXT_FIELDS:
            value = item[name]
            if not isinstance(value, str) or not value.strip():
                raise DraftError(f"第 {index} 条 {name} 为空或不是文本")
            values[name] = value.strip()
        if len(values["category"]) > CATEGORY_MAX_CHARS:
            raise DraftError(f"第 {index} 条分类超过 {CATEGORY_MAX_CHARS} 字")
        keywords = item["keywords"]
        if not isinstance(keywords, list) or not all(
            isinstance(word, str) and word.strip() for word in keywords
        ):
            raise DraftError(f"第 {index} 条关键词必须是非空文本组成的列表")
        draft = DraftEntry(keywords=tuple(word.strip() for word in keywords), **values)
        if draft.identity in seen:
            raise DraftError(f"第 {index} 条与第 {seen[draft.identity]} 条重复")
        seen[draft.identity] = index
        drafts.append(draft)
    return drafts


def summarize(drafts: Sequence[DraftEntry]) -> DraftSummary:
    """统计数量、分类与仍含「待填写」的条数，并给出内容摘要用于核对是同一份输入。"""
    canonical = json.dumps(
        [
            [draft.category, draft.question_zh, draft.answer_zh,
             draft.question_en, draft.answer_en, list(draft.keywords)]
            for draft in drafts
        ],
        ensure_ascii=False,
    )
    return DraftSummary(
        count=len(drafts),
        categories=dict(Counter(draft.category for draft in drafts)),
        placeholders=sum(
            1 for draft in drafts if _PLACEHOLDER.search(f"{draft.answer_zh}\n{draft.answer_en}")
        ),
        input_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


async def plan_import(session: AsyncSession, drafts: Sequence[DraftEntry]) -> ImportPlan:
    """对照现有知识：完全相同的跳过，身份相同但内容不同的列为冲突，其余新建。

    启用与否不参与比较：已启用的同一条知识照样跳过，不会被强制停用。
    """
    existing = {
        (_canonical(entry.category), _canonical(entry.question_zh), _canonical(entry.question_en)):
        entry
        for entry in await session.scalars(select(KnowledgeEntry))
    }
    new: list[DraftEntry] = []
    skipped = 0
    conflicts: list[int] = []
    for index, draft in enumerate(drafts, start=1):
        match = existing.get(draft.identity)
        if match is None:
            new.append(draft)
        elif draft.same_content(match):
            skipped += 1
        else:
            conflicts.append(index)
    return ImportPlan(new=new, skipped=skipped, conflicts=conflicts)


async def _require_admin(session: AsyncSession, admin_id: int) -> Employee:
    """操作人必须是真实存在、在职的管理员。"""
    employee = await session.get(Employee, admin_id)
    if employee is None or not employee.is_active or employee.role is not EmployeeRole.ADMIN:
        raise DraftError(f"员工 {admin_id} 不是在职管理员")
    return employee


def _audit(session: AsyncSession, admin: Employee, entry_id: int) -> None:
    """沿用后台知识审计口径：只记动作与条目编号，不复制正文。"""
    session.add(
        AuditLog(
            actor_employee_id=admin.id,
            action=AUDIT_ACTION,
            target_type="knowledge_entry",
            target_id=str(entry_id),
            details={"entry_id": entry_id},
        )
    )


async def apply_import(
    factory: async_sessionmaker[AsyncSession],
    drafts: Sequence[DraftEntry],
    *,
    admin_id: int,
) -> ImportResult:
    """在一个事务里重新核对冲突并写入全部新条目；任何错误整批回滚。"""
    async with factory() as session, session.begin():
        if session.bind.dialect.name == "postgresql":
            # 知识表没有唯一约束；串行化两次导入，避免并发运行各自判定为新建。
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            )
        admin = await _require_admin(session, admin_id)
        # 预览到写入之间可能有人在后台改过知识，写入前在同一事务里重新比对。
        plan = await plan_import(session, drafts)
        if plan.conflicts:
            raise ImportConflictError(f"第 {plan.conflicts} 条与已有知识冲突，未写入任何条目")
        created: list[KnowledgeEntry] = []
        for draft in plan.new:
            entry = KnowledgeEntry(
                category=draft.category,
                question_zh=draft.question_zh,
                answer_zh=draft.answer_zh,
                question_en=draft.question_en,
                answer_en=draft.answer_en,
                keywords=list(draft.keywords),
                # 从第一次提交起就是停用：客人检索与向量补齐都只读启用条目。
                is_enabled=False,
                updated_by=admin.id,
            )
            session.add(entry)
            created.append(entry)
        await session.flush()
        for entry in created:
            _audit(session, admin, entry.id)
        await session.flush()
        return ImportResult(created_ids=[entry.id for entry in created], skipped=plan.skipped)


def _print_summary(summary: DraftSummary, target: str) -> None:
    """打印目标与输入概况，不含正文。"""
    print(f"目标数据库：{target}")
    print(f"输入：{summary.count} 条，sha256={summary.input_digest}")
    print(f"仍含「待填写」：{summary.placeholders} 条")
    print("分类：" + "，".join(f"{name} {count}" for name, count in summary.categories.items()))


async def _run(args: argparse.Namespace, raw: bytes) -> int:
    """命令行主流程：解析、预览，按需写入。"""
    from homestay_bot.config import BootstrapSettings
    from homestay_bot.db import create_engine, create_session_factory

    drafts = parse_drafts(raw)
    database_url = BootstrapSettings().database_url  # type: ignore[call-arg]
    url = make_url(database_url)
    # 只标出后端与库名，便于确认连的是哪个环境；SQLite 只给文件名，不暴露本机路径。
    name = PurePath(url.database or "").name if url.get_backend_name() == "sqlite" else url.database
    target = f"{url.get_backend_name()} / {name}"
    _print_summary(summarize(drafts), target)

    engine = create_engine(database_url)
    try:
        factory = create_session_factory(engine)
        async with factory() as session:
            await _require_admin(session, args.admin_id)
            plan = await plan_import(session, drafts)
        print(f"预览：新建 {len(plan.new)}，跳过 {plan.skipped}，冲突 {len(plan.conflicts)}")
        if plan.conflicts:
            print(f"冲突序号：{plan.conflicts}；不会写入任何条目")
            return 2
        if not args.apply:
            print("只读预览，未写入。确认无误后加 --apply 执行。")
            return 0
        result = await apply_import(factory, drafts, admin_id=args.admin_id)
        print(f"已写入：新建 {len(result.created_ids)}，跳过 {result.skipped}，全部为停用状态")
        print(f"新条目编号：{result.created_ids}")
        return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """解析参数并从标准输入读取草稿。"""
    parser = argparse.ArgumentParser(description="以停用状态一次性导入知识草稿")
    parser.add_argument("--admin-id", type=int, required=True, help="执行导入的在职管理员员工编号")
    parser.add_argument("--apply", action="store_true", help="实际写入；缺省只预览")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args, sys.stdin.buffer.read()))
    except (DraftError, ImportConflictError) as error:
        print(f"未写入：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

