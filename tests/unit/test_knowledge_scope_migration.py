"""在临时数据库验证知识范围迁移，不使用应用默认连接。"""

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_knowledge_scope_upgrade_preserves_old_content_and_constraints(tmp_path):
    """启用资料默认分类，停用待审核，正文保留；错误范围/日期被数据库拒绝，降升可重放。"""
    database = tmp_path / "knowledge.db"
    environment = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{database}")

    def migrate(action, revision):
        """显式指向独立临时库执行 Alembic。"""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", action, revision],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "0028_guest_plaintext")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO knowledge_entries "
            "(id,category,question_zh,answer_zh,question_en,answer_en,keywords,"
            "is_enabled) VALUES (1,'早餐','早餐?','7点','Breakfast?','At 7','[]',1)"
        )
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO knowledge_entries "
            "(id,category,question_zh,answer_zh,question_en,answer_en,keywords,is_enabled) "
            "VALUES (?,?,?,?,?,?,'[]',?)",
            [
                (2, "交通", "附近停车?", "合成", "Parking nearby?", "synthetic", 1),
                (3, "交通", "停车?", "合成", "Nearby parking?", "synthetic", 1),
                (4, "早餐", "早餐?", "合成", "Breakfast?", "synthetic", 0),
                (5, "紧急处置", "火灾处置", "楼下有早餐店", "Fire", "synthetic", 1),
            ],
        )
    migrate("upgrade", "0029_knowledge_scope")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT scope,answer_zh,is_enabled FROM knowledge_entries WHERE id=1"
        ).fetchone() == (
            "global",
            "7点",
            1,
        )
        assert connection.execute(
            "SELECT id,scope FROM knowledge_entries ORDER BY id"
        ).fetchall() == [
            (1, "global"),
            (2, "public"),
            (3, "public"),
            (4, "unreviewed"),
            (5, "global"),
        ]
        for sql in (
            "UPDATE knowledge_entries SET scope='property'",
            "UPDATE knowledge_entries SET scope='wrong'",
            "UPDATE knowledge_entries SET valid_from='2026-09-26',valid_until='2026-09-25'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
        connection.execute("UPDATE knowledge_entries SET scope='global' WHERE id=1")
    migrate("downgrade", "0028_guest_plaintext")
    migrate("upgrade", "0029_knowledge_scope")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT scope,answer_zh FROM knowledge_entries").fetchone() == (
            "global",
            "7点",
        )

    # 仓储和复核工具用的是最新模型：先迁到最新版本（含 0032 触发词两列）再读，
    # 同时确认 0032 只加可空列，0029 迁移出的范围不受影响。
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT trigger_any, trigger_exclude FROM knowledge_entries WHERE id=1"
        ).fetchone() == (None, None)

    async def verify_readers():
        """真实仓储和复核工具读取迁移结果，保证启用知识不会被范围默认值吞掉。"""
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
        from homestay_bot.tools.knowledge_scope_report import scope_report
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        try:
            async with AsyncSession(engine) as session:
                active = await SQLAlchemyKnowledgeRepository(session).list_active()
                assert [row.id for row in active] == [1, 2, 3, 5]
                report = await scope_report(session)
                assert [row["id"] for row in report["public"]] == [2, 3]
        finally:
            await engine.dispose()
    asyncio.run(verify_readers())
