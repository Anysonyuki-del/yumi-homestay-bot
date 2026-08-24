"""验证管理员诊断聚合只使用安全只读投影。"""

from datetime import UTC, datetime

import pytest

from homestay_bot.services.admin_diagnostics_service import AdminDiagnosticsService


class HealthStub:
    """返回固定或异常健康状态。"""

    def __init__(self, *, fail: bool = False) -> None:
        """保存失败开关。"""
        self.fail = fail

    async def check(self) -> dict[str, str]:
        """返回受控状态或携敏感正文的异常。"""
        if self.fail:
            raise RuntimeError("raw-secret-query")
        return {"status": "ok", "database": "ok", "configuration": "ok"}


class RepositoryStub:
    """提供任务和审计的安全投影。"""

    def __init__(
        self,
        *,
        fail_counts: bool = False,
        fail_errors: bool = False,
        fail_revision: bool = False,
    ) -> None:
        """保存各独立探针的失败开关。"""
        self.fail_counts = fail_counts
        self.fail_errors = fail_errors
        self.fail_revision = fail_revision

    async def job_status_counts(self):
        """返回任务状态计数。"""
        if self.fail_counts:
            raise RuntimeError("counts-secret-query")
        return {"pending": 2, "failed": 1}

    async def configuration_revision(self):
        """返回未发布 registry 时仍可观察的数据库 revision。"""
        if self.fail_revision:
            raise RuntimeError("revision-secret-query")
        return 5

    async def recent_job_error_codes(self, *, limit: int):
        """返回稳定错误码，不返回 payload。"""
        if self.fail_errors:
            raise RuntimeError("errors-secret-query")
        return ("timeout", "rate_limited")[:limit]

    async def list_audits(self, *, offset: int, limit: int):
        """用 limit+1 返回稳定分页投影。"""
        assert limit == 3
        return tuple(
            {"id": value, "action": "admin_debug_preview", "created_at": "safe"}
            for value in (9, 8, 7)
        )


class RuntimeStatusStub:
    """提供当前配置 revision。"""

    def __init__(self, *, fail: bool = False) -> None:
        """保存运行状态探针失败开关。"""
        self.fail = fail

    async def status(self):
        """返回无秘密 registry 状态。"""
        if self.fail:
            raise RuntimeError("registry-secret-query")
        return type("Status", (), {"revision": 12})()


class EmptyRepositoryStub(RepositoryStub):
    """返回成功读取但没有任务或错误码的安全投影。"""

    async def job_status_counts(self):
        """明确表示当前没有持久化任务。"""
        return {}

    async def recent_job_error_codes(self, *, limit: int):
        """明确表示查询成功且没有错误码。"""
        return ()


@pytest.mark.asyncio
async def test_diagnostics_builds_safe_report_and_limit_plus_one_page() -> None:
    """报告只包含状态、版本、revision和稳定错误码。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=RepositoryStub(),
        registry=RuntimeStatusStub(),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()
    page = await service.list_audits(page=2, page_size=2)

    assert snapshot.configuration_revision == 12
    assert snapshot.configuration_revision_source == "runtime"
    assert snapshot.health_available is True
    assert snapshot.job_status_counts == {"pending": 2, "failed": 1}
    assert snapshot.recent_job_error_codes == ("timeout", "rate_limited")
    assert "raw-secret" not in snapshot.report_text
    assert "1.2.3" in snapshot.report_text
    assert [item["id"] for item in page.items] == [9, 8]
    assert page.has_next is True


@pytest.mark.asyncio
async def test_health_exception_degrades_without_exception_body() -> None:
    """健康检查异常不得让诊断页失败或把异常正文写进报告。"""
    service = AdminDiagnosticsService(
        health=HealthStub(fail=True),
        repository=RepositoryStub(),
        registry=RuntimeStatusStub(),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.health == {"status": "degraded"}
    assert snapshot.health_available is False
    assert "组件状态：无法读取" in snapshot.report_text
    assert "raw-secret-query" not in snapshot.report_text


@pytest.mark.asyncio
async def test_diagnostics_uses_database_revision_before_runtime_publish() -> None:
    """运行客户端未发布时诊断和审计仍可使用数据库 revision。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=RepositoryStub(),
        registry=None,
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.configuration_revision == 5
    assert snapshot.configuration_revision_source == "database"
    assert "未确认已生效" in snapshot.report_text


@pytest.mark.asyncio
async def test_job_probe_failures_keep_successful_sibling_data() -> None:
    """任务子探针必须独立降级，不能用整组空值掩盖读取失败。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=RepositoryStub(fail_counts=True),
        registry=RuntimeStatusStub(),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.job_status_counts is None
    assert snapshot.recent_job_error_codes == ("timeout", "rate_limited")
    assert "任务状态：无法读取" in snapshot.report_text
    assert "最近错误码：timeout、rate_limited" in snapshot.report_text
    assert "counts-secret-query" not in snapshot.report_text


@pytest.mark.asyncio
async def test_error_code_probe_failure_is_not_reported_as_no_errors() -> None:
    """错误码读取失败必须显示无法读取，不能谎报为没有错误。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=RepositoryStub(fail_errors=True),
        registry=RuntimeStatusStub(),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.job_status_counts == {"pending": 2, "failed": 1}
    assert snapshot.recent_job_error_codes is None
    assert "最近错误码：无法读取" in snapshot.report_text
    assert "最近错误码：无" not in snapshot.report_text.splitlines()
    assert "errors-secret-query" not in snapshot.report_text


@pytest.mark.asyncio
async def test_runtime_revision_failure_falls_back_to_labeled_database_revision() -> None:
    """运行 revision 不可读时保留数据库记录，但不得冒充已生效值。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=RepositoryStub(),
        registry=RuntimeStatusStub(fail=True),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.configuration_revision == 5
    assert snapshot.configuration_revision_source == "database"
    assert snapshot.health["status"] == "degraded"
    assert "数据库配置 revision：5（未确认已生效）" in snapshot.report_text
    assert "registry-secret-query" not in snapshot.report_text


@pytest.mark.asyncio
async def test_successful_empty_job_probes_report_no_data() -> None:
    """只有探针成功且为空时，报告才可以明确显示没有任务和错误。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=EmptyRepositoryStub(),
        registry=RuntimeStatusStub(),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.job_status_counts == {}
    assert snapshot.recent_job_error_codes == ()
    assert "任务状态：无任务" in snapshot.report_text.splitlines()
    assert "最近错误码：无" in snapshot.report_text.splitlines()


@pytest.mark.asyncio
async def test_all_revision_probes_fail_without_leaking_exception_text() -> None:
    """运行和数据库 revision 都失败时必须明确不可读且不泄露异常。"""
    service = AdminDiagnosticsService(
        health=HealthStub(),
        repository=RepositoryStub(fail_revision=True),
        registry=RuntimeStatusStub(fail=True),
        started_at=datetime(2026, 8, 11, tzinfo=UTC),
        version="1.2.3",
    )

    snapshot = await service.snapshot()

    assert snapshot.configuration_revision is None
    assert snapshot.configuration_revision_source == "unavailable"
    assert "配置 revision：无法读取" in snapshot.report_text
    assert "registry-secret-query" not in snapshot.report_text
    assert "revision-secret-query" not in snapshot.report_text
