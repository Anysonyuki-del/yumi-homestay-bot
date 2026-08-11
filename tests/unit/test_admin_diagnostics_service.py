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

    async def job_status_counts(self):
        """返回任务状态计数。"""
        return {"pending": 2, "failed": 1}

    async def configuration_revision(self):
        """返回未发布 registry 时仍可观察的数据库 revision。"""
        return 5

    async def recent_job_error_codes(self, *, limit: int):
        """返回稳定错误码，不返回 payload。"""
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

    async def status(self):
        """返回无秘密 registry 状态。"""
        return type("Status", (), {"revision": 12})()


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
