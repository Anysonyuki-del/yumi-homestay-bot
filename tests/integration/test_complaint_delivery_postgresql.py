"""客诉在途判定与条件更新在 PostgreSQL 上的验证（Spec §4.1.1、AC01a）。

条件更新的互斥要在目标数据库上证明：SQLite 串行化写入，测不出真正并发的两个
事务。这里复用 SQLite 版本的交错用例，并补两条真正并发提交的用例。需要设置
YUMI_TEST_POSTGRES_URL 指向本机隔离测试库，否则整组跳过。
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from homestay_bot import application
from homestay_bot.domain.enums import ComplaintReviewStatus
from homestay_bot.domain.models import ComplaintReview, Job
from tests.integration import test_complaint_delivery_guards as guards
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401


@pytest_asyncio.fixture
async def factory(pg_engine):  # noqa: F811
    """隔离测试库里放入与 SQLite 版本相同的合成客诉。"""
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    await guards.seed_review(session_factory)
    return session_factory


# 与 SQLite 版本同一组跨 Session 交错用例，换到目标数据库上再跑一遍。
test_stale_cancel_cannot_override_committed_send = (
    guards.test_stale_cancel_cannot_override_committed_send
)
test_stale_version_send_is_rejected_after_concurrent_save = (
    guards.test_stale_version_send_is_rejected_after_concurrent_save
)
test_resend_close_and_return_refused_while_original_job_retries = (
    guards.test_resend_close_and_return_refused_while_original_job_retries
)
test_retry_after_terminal_failure_registers_one_new_attempt = (
    guards.test_retry_after_terminal_failure_registers_one_new_attempt
)
test_late_result_of_old_attempt_does_not_override_new_attempt = (
    guards.test_late_result_of_old_attempt_does_not_override_new_attempt
)
test_sent_and_cancelled_reviews_are_read_only = guards.test_sent_and_cancelled_reviews_are_read_only
test_late_analysis_cannot_reopen_a_cancelled_review = (
    guards.test_late_analysis_cannot_reopen_a_cancelled_review
)
test_failed_card_registration_rolls_back_ready_state = (
    guards.test_failed_card_registration_rolls_back_ready_state
)


async def _state(factory) -> tuple[ComplaintReviewStatus, int, int]:
    """返回客诉最终状态、版本和出站任务数。"""
    async with factory() as session:
        review = await session.get(ComplaintReview, guards.REVIEW_ID)
        jobs = list(await session.scalars(select(Job).where(Job.job_type == "wecom_send_text")))
        return review.status, review.version, len(jobs)


@pytest.mark.asyncio
async def test_two_concurrent_sends_of_the_same_version_register_one_job(factory) -> None:
    """同版本两次发送真正并发提交：只有一个成功，只留一个出站任务。"""
    service = application.SessionComplaintAdminService(factory)

    results = await asyncio.gather(
        service.send(guards.REVIEW_ID, 1, "甲的回复", employee_id=1),
        service.send(guards.REVIEW_ID, 1, "乙的回复", employee_id=2),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    assert all(
        result is None or isinstance(result, ValueError) for result in results
    ), results
    assert await _state(factory) == (ComplaintReviewStatus.SEND_QUEUED, 2, 1)


@pytest.mark.asyncio
async def test_concurrent_send_and_cancel_leave_a_consistent_record(factory) -> None:
    """发送与关闭同时提交：要么已排队且有一个任务，要么已关闭且没有任务，不会两样都有。"""
    service = application.SessionComplaintAdminService(factory)

    results = await asyncio.gather(
        service.send(guards.REVIEW_ID, 1, "回复", employee_id=1),
        service.cancel(guards.REVIEW_ID, 1, employee_id=2),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1, results
    assert await _state(factory) in {
        (ComplaintReviewStatus.SEND_QUEUED, 2, 1),
        (ComplaintReviewStatus.CANCELLED, 2, 0),
    }
