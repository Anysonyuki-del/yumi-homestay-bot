"""失败回执补匹配在 PostgreSQL 上的验证（Spec 2026-11-19 F1、M3）。

SQLite 不检查 VARCHAR 长度，测不出去重键超过 Job.dedupe_key 列宽；提交交错也要在
目标数据库的读已提交隔离下再证明一次。这里复用 SQLite 版本的全部用例，各会话走
独立连接。需要设置 YUMI_TEST_POSTGRES_URL 指向本机隔离测试库，否则整组跳过。
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration import test_send_failure_rematch as rematch
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401


@pytest_asyncio.fixture
async def factory(pg_engine):  # noqa: F811
    """隔离测试库里放入与 SQLite 版本相同的合成会话。"""
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    await rematch.seed_conversation(session_factory)
    return session_factory


# 与 SQLite 版本同一组用例，换到目标数据库上再跑一遍。
test_receipt_before_record_is_rematched = rematch.test_receipt_before_record_is_rematched
test_record_before_receipt_needs_no_rematch = rematch.test_record_before_receipt_needs_no_rematch
test_duplicate_receipt_enqueues_one_rematch_and_one_action = (
    rematch.test_duplicate_receipt_enqueues_one_rematch_and_one_action
)
test_long_ids_produce_bounded_distinct_dedupe_keys = (
    rematch.test_long_ids_produce_bounded_distinct_dedupe_keys
)
test_unmatched_receipt_ends_failed_with_its_own_error_code = (
    rematch.test_unmatched_receipt_ends_failed_with_its_own_error_code
)
test_rematch_storage_error_rolls_back_and_retries = (
    rematch.test_rematch_storage_error_rolls_back_and_retries
)
test_complaint_reply_is_never_retried_as_ordinary_text = (
    rematch.test_complaint_reply_is_never_retried_as_ordinary_text
)
test_send_commit_right_after_complaint_lookup_is_not_misread = (
    rematch.test_send_commit_right_after_complaint_lookup_is_not_misread
)
