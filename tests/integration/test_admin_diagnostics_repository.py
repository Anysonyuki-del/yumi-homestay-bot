"""验证诊断仓储只选择安全字段并稳定分页。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import EmployeeRole, JobStatus
from homestay_bot.domain.models import AuditLog, Base, Employee, Job, PropertyProfile
from homestay_bot.repositories.admin_diagnostics import SQLAlchemyAdminDiagnosticsRepository


@pytest.mark.asyncio
async def test_repository_projects_safe_fields_and_stable_limit_plus_one() -> None:
    """诊断不得返回 job payload、审计 details、UID、密钥或房源隐私字段。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, tzinfo=UTC)
    async with factory() as session:
        session.add_all(
            [
                Employee(
                    id=1,
                    wecom_userid="UID-SECRET",
                    name="管理员",
                    role=EmployeeRole.ADMIN,
                    is_active=True,
                ),
                PropertyProfile(
                    id=11,
                    title="江汉路一号房",
                    address_hint="LOCK-SECRET",
                    is_active=True,
                ),
                PropertyProfile(id=12, title="停用房", is_active=False),
                Job(
                    job_type="safe_type",
                    payload={"message": "RAW-MESSAGE-SECRET"},
                    status=JobStatus.FAILED,
                    attempts=3,
                    available_at=now,
                    last_error_code="timeout",
                    updated_at=now,
                ),
                Job(
                    job_type="safe_type",
                    payload={"url": "https://example.test/?token=SECRET"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=now,
                    last_error_code="rate_limited",
                    updated_at=now - timedelta(minutes=1),
                ),
            ]
        )
        session.add_all(
            [
                AuditLog(
                    actor_employee_id=1,
                    action=f"safe_action_{index}",
                    target_type="system",
                    target_id="UID-SECRET",
                    details={"raw": "RAW-MESSAGE-SECRET", "secret": "TOKEN"},
                )
                for index in range(4)
            ]
        )
        await session.commit()

        repository = SQLAlchemyAdminDiagnosticsRepository(session)
        properties = await repository.list_debug_properties()
        counts = await repository.job_status_counts()
        errors = await repository.recent_job_error_codes(limit=2)
        audits = await repository.list_audits(offset=0, limit=3)

    assert properties == (type(properties[0])(11, "江汉路一号房"),)
    assert counts == {"failed": 1, "pending": 1}
    assert errors == ("timeout", "rate_limited")
    assert len(audits) == 3
    assert [item.id for item in audits] == sorted(
        [item.id for item in audits], reverse=True
    )
    rendered = repr((properties, counts, errors, audits))
    for secret in (
        "UID-SECRET",
        "LOCK-SECRET",
        "RAW-MESSAGE-SECRET",
        "https://",
        "TOKEN",
    ):
        assert secret not in rendered
    await engine.dispose()


@pytest.mark.asyncio
async def test_debug_audit_whitelists_metadata_without_question_or_reply() -> None:
    """调试审计只落白名单元数据，且可由诊断安全投影读取。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Employee(
                id=1,
                wecom_userid="admin-uid",
                name="管理员",
                role=EmployeeRole.ADMIN,
                is_active=True,
            )
        )
        await session.flush()
        repository = SQLAlchemyAdminDiagnosticsRepository(session)
        await repository.record_debug_preview(
            actor_employee_id=1,
            question_hash="a" * 64,
            question_length=7,
            intent="faq",
            tool_names=["list_properties"],
            succeeded=True,
            question="原始问题不得保存",
            reply_text="原始回复不得保存",
        )
        await session.commit()
        entries = await repository.list_audits(offset=0, limit=2)

    assert len(entries) == 1
    assert "原始问题" not in repr(entries)
    assert "原始回复" not in repr(entries)
    await engine.dispose()


@pytest.mark.asyncio
async def test_debug_audit_rejects_hostile_intent_and_tool_names_in_sqlite() -> None:
    """仓储层再次过滤敌对机器码，AuditLog JSON 不得含身份、query 或 secret。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Employee(
                id=1,
                wecom_userid="admin-uid",
                name="管理员",
                role=EmployeeRole.ADMIN,
                is_active=True,
            )
        )
        await session.flush()
        repository = SQLAlchemyAdminDiagnosticsRepository(session)
        await repository.record_debug_preview(
            actor_employee_id=1,
            question_hash="b" * 64,
            question_length=8,
            intent="UID_13800138000?token=secret",
            tool_names=[
                "search_reference_price",
                "send_text?token=secret",
                "list_properties",
                "search_reference_price",
                "search_availability",
                "create_reservation_UID_13800138000",
            ],
            succeeded=True,
        )
        await session.commit()
        audit = await session.scalar(select(AuditLog))

    assert audit is not None
    assert audit.details == {
        "question_hash": "b" * 64,
        "question_length": 8,
        "intent": "unknown",
        "tool_names": [
            "list_properties",
            "search_availability",
            "search_reference_price",
        ],
        "succeeded": True,
    }
    serialized = repr(audit.details)
    for secret in ("13800138000", "UID", "token=", "secret", "send_text", "create_reservation"):
        assert secret not in serialized
    await engine.dispose()
