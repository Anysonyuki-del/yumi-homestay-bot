"""迁移 0026 对存量「重试在途」标记的收敛判据。"""

import json
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_module() -> object:
    """按 Alembic 自己的方式加载迁移模块，避免手写导入路径。"""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    return script.get_revision("0026_settle_retry_latch").module


@pytest.fixture()
def messages_db(tmp_path: Path):
    """建一张最小 messages 表，装入生产上那三条的真实形态。

    52 的改写失败但已就地通知、55 与 105 的改写被受理——三条都已了结却仍挂着
    闩锁；另加一条重试确无结果的，用来证明判据不会把在途的一起清掉。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, metadata TEXT NOT NULL)"
        ))
        rows = {
            52: {"delivery_status": "failed", "delivery_retry_pending": True},
            53: {"delivery_status": "failed", "retry_of_message_id": "52",
                 "delivery_failure_notified": True},
            55: {"delivery_status": "failed", "delivery_retry_pending": True},
            56: {"delivery_status": "accepted", "retry_of_message_id": "55"},
            105: {"delivery_status": "failed", "delivery_retry_pending": True},
            106: {"delivery_status": "accepted", "retry_of_message_id": "105"},
            200: {"delivery_status": "failed", "delivery_retry_pending": True},
            201: {"delivery_status": "sent", "retry_of_message_id": "200"},
        }
        for message_id, metadata in rows.items():
            connection.execute(
                text("INSERT INTO messages VALUES (:i, :m)"),
                {"i": message_id, "m": json.dumps(metadata)},
            )
    return engine


def _pending(engine, message_id: int) -> object:
    with engine.begin() as connection:
        raw = connection.execute(
            text("SELECT metadata FROM messages WHERE id = :i"), {"i": message_id}
        ).scalar_one()
    return json.loads(raw).get("delivery_retry_pending")


def test_the_migration_settles_exactly_the_finished_chains(messages_db, monkeypatch):
    """已了结的三条清掉闩锁，重试确无结果的那条原样保留。"""
    module = _upgrade_module()
    with messages_db.begin() as connection:
        monkeypatch.setattr(module.op, "get_bind", lambda: connection)
        monkeypatch.setattr(module.context, "is_offline_mode", lambda: False)
        module.upgrade()

    assert _pending(messages_db, 52) is False, "改写失败但已通知，链已了结"
    assert _pending(messages_db, 55) is False, "改写已被受理"
    assert _pending(messages_db, 105) is False, "改写已被受理"
    assert _pending(messages_db, 200) is True, "重试尚无结果，不得清掉闩锁"


def test_the_migration_never_touches_delivery_status(messages_db, monkeypatch):
    """收敛的只是闩锁：受理不等于送达，投递状态一个字都不能改。"""
    module = _upgrade_module()
    with messages_db.begin() as connection:
        monkeypatch.setattr(module.op, "get_bind", lambda: connection)
        monkeypatch.setattr(module.context, "is_offline_mode", lambda: False)
        module.upgrade()
        after = {
            row[0]: json.loads(row[1]).get("delivery_status")
            for row in connection.execute(text("SELECT id, metadata FROM messages"))
        }

    assert after[52] == "failed"
    assert after[55] == "failed"
    assert after[105] == "failed"
