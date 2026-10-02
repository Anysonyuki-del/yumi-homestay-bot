"""审批人工核验与建单轮次保护在 PostgreSQL 上的验证（Spec §5、§6）。

SQLite 与 PostgreSQL 的事务、行锁语义不同，交错用例要在生产数据库上再跑一遍。
复用 SQLite 版本的用例；需要设置 YUMI_TEST_POSTGRES_URL，否则整组跳过。
"""

import pytest_asyncio

from homestay_bot.db import create_session_factory
from tests.integration import test_approval_review_actions as review
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401


@pytest_asyncio.fixture
async def world(pg_engine):  # noqa: F811
    """隔离测试库里放入与 SQLite 版本相同的合成审批。"""
    factory = create_session_factory(pg_engine)
    sensitive = review.new_sensitive()
    await review.seed_world(factory, sensitive)
    return factory, sensitive


# A1 唯一键竞争、A2 轮次指纹、AR3 真实门面建单、AR5 回填原子性、AR6 迟到结果。
test_stale_reopen_cannot_undo_a_newer_confirmation_round = (
    review.test_stale_reopen_cannot_undo_a_newer_confirmation_round
)
test_confirm_through_the_real_session_facade_reaches_booked = (
    review.test_confirm_through_the_real_session_facade_reaches_booked
)
test_unverifiable_creation_through_facade_becomes_needs_review_with_request_id = (
    review.test_unverifiable_creation_through_facade_becomes_needs_review_with_request_id
)
test_backfill_collision_is_refused_without_partial_writes = (
    review.test_backfill_collision_is_refused_without_partial_writes
)
test_backfill_audit_failure_leaves_nothing_behind = (
    review.test_backfill_audit_failure_leaves_nothing_behind
)
test_backfill_rolled_back_by_the_caller_is_fully_undone = (
    review.test_backfill_rolled_back_by_the_caller_is_fully_undone
)
test_repeat_confirm_while_creation_is_in_flight_changes_nothing = (
    review.test_repeat_confirm_while_creation_is_in_flight_changes_nothing
)
test_late_result_of_an_old_round_cannot_overwrite_the_new_round = (
    review.test_late_result_of_an_old_round_cannot_overwrite_the_new_round
)
# AR8（第三轮审查新增）：新一轮仍在创建中时，靠确认时间识别轮次。
test_late_result_cannot_overwrite_a_newer_round_that_is_still_creating = (
    review.test_late_result_cannot_overwrite_a_newer_round_that_is_still_creating
)
test_discarded_result_keeps_creation_and_verification_stages = (
    review.test_discarded_result_keeps_creation_and_verification_stages
)
test_late_audit_failure_rolls_back_but_preserves_diagnostics = (
    review.test_late_audit_failure_rolls_back_but_preserves_diagnostics
)
test_creation_error_does_not_claim_verified_rejection = (
    review.test_creation_error_does_not_claim_verified_rejection
)
