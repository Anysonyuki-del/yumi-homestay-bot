"""持久化不可变运行配置版本和带乐观锁的激活指针。"""

import re
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import RuntimeConfigVersionStatus
from homestay_bot.domain.models import (
    AuditLog,
    RuntimeConfigState,
    RuntimeConfigVersion,
)


class RuntimeConfigConflictError(RuntimeError):
    """表示配置指针已被另一个请求推进。"""


class RuntimeConfigRollbackError(RuntimeError):
    """表示当前没有可安全回滚的上一版本。"""


class SQLAlchemyRuntimeConfigRepository:
    """使用单例状态行和 CAS 更新保证跨实例激活一致性。"""

    _AUDIT_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    _AUDIT_RESULT_VALUES = {"ok", "failed", "conflict"}
    _AUDIT_ERROR_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

    def __init__(self, session: AsyncSession) -> None:
        """绑定由调用方控制提交边界的数据库会话。"""
        self._session = session

    async def get_state(self) -> RuntimeConfigState:
        """原子确保单例状态存在，并返回当前指针快照。"""
        await self._ensure_state()
        state = await self._session.get(RuntimeConfigState, 1)
        if state is None:
            raise RuntimeError("运行配置状态初始化失败")
        return state

    async def get_version(self, version_id: int) -> RuntimeConfigVersion | None:
        """按编号读取不可变配置版本。"""
        return await self._session.get(
            RuntimeConfigVersion,
            version_id,
            populate_existing=True,
        )

    async def get_active_version(self) -> RuntimeConfigVersion | None:
        """读取当前激活版本；首次使用环境配置时返回 None。"""
        _, version = await self.get_activation_context()
        return version

    async def get_activation_context(
        self,
    ) -> tuple[RuntimeConfigState, RuntimeConfigVersion | None]:
        """用一条查询一致读取状态及其指向的 active 版本。"""
        await self._ensure_state()
        row = (
            await self._session.execute(
                select(RuntimeConfigState, RuntimeConfigVersion)
                .outerjoin(
                    RuntimeConfigVersion,
                    RuntimeConfigState.active_version_id == RuntimeConfigVersion.id,
                )
                .where(RuntimeConfigState.id == 1)
            )
        ).one_or_none()
        if row is None:
            raise RuntimeError("运行配置状态初始化失败")
        return cast(RuntimeConfigState, row[0]), cast(RuntimeConfigVersion | None, row[1])

    async def list_versions(self, *, limit: int = 20) -> list[RuntimeConfigVersion]:
        """按新到旧返回有界版本历史，页面只使用掩码摘要。"""
        if not 1 <= limit <= 100:
            raise ValueError("配置版本分页大小无效")
        result = await self._session.scalars(
            select(RuntimeConfigVersion).order_by(RuntimeConfigVersion.id.desc()).limit(limit)
        )
        return list(result.all())

    async def create_candidate(
        self,
        encrypted_payload: bytes,
        masked_summary: dict[str, object],
        actor_id: int,
        *,
        based_on_version_id: int | None = None,
        based_on_revision: int = 0,
    ) -> RuntimeConfigVersion:
        """写入一整份不可变密文及不含秘密的页面摘要。"""
        if not encrypted_payload:
            raise ValueError("配置密文不能为空")
        version = RuntimeConfigVersion(
            encrypted_payload=encrypted_payload,
            masked_summary=masked_summary,
            created_by=actor_id,
            status=RuntimeConfigVersionStatus.CANDIDATE,
            test_results={},
            based_on_version_id=based_on_version_id,
            based_on_revision=based_on_revision,
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def mark_test_passed(
        self,
        version_id: int,
        test_results: dict[str, object],
    ) -> None:
        """把仍为候选的版本标记为测试通过，并保存脱敏结果。"""
        results = self._validate_test_results(test_results, succeeded=True)
        statement = (
            update(RuntimeConfigVersion)
            .where(
                RuntimeConfigVersion.id == version_id,
                RuntimeConfigVersion.status == RuntimeConfigVersionStatus.CANDIDATE,
            )
            .values(
                status=RuntimeConfigVersionStatus.TEST_PASSED,
                test_results=results,
                failure_code=None,
            )
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("配置候选状态已变化")

    async def mark_test_failed(
        self,
        version_id: int,
        test_results: dict[str, object],
        *,
        failure_code: str,
    ) -> None:
        """持久化失败候选的安全结果，且保持它不可激活。"""
        if self._AUDIT_ERROR_PATTERN.fullmatch(failure_code) is None:
            raise ValueError("配置测试失败码无效")
        results = self._validate_test_results(test_results, succeeded=False)
        statement = (
            update(RuntimeConfigVersion)
            .where(
                RuntimeConfigVersion.id == version_id,
                RuntimeConfigVersion.status == RuntimeConfigVersionStatus.CANDIDATE,
            )
            .values(
                status=RuntimeConfigVersionStatus.TEST_FAILED,
                test_results=results,
                failure_code=failure_code,
            )
        )
        result = await self._session.execute(statement)
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("配置候选状态已变化")

    async def mark_activation_conflict(self, version_id: int) -> None:
        """把已测试但失去基线修订号的候选标记为激活冲突。"""
        statement = (
            update(RuntimeConfigVersion)
            .where(
                RuntimeConfigVersion.id == version_id,
                RuntimeConfigVersion.status == RuntimeConfigVersionStatus.TEST_PASSED,
            )
            .values(
                status=RuntimeConfigVersionStatus.ACTIVATION_CONFLICT,
                failure_code="revision_conflict",
            )
        )
        await self._session.execute(statement)

    async def activate(
        self,
        version_id: int,
        expected_revision: int,
    ) -> RuntimeConfigState:
        """以 revision 为 CAS 条件激活候选，并保存原 active 为 previous。"""
        version = await self._require_version(version_id)
        if version.status is not RuntimeConfigVersionStatus.TEST_PASSED:
            raise LookupError("运行配置候选尚未通过测试")
        if version.based_on_revision != expected_revision:
            raise RuntimeConfigConflictError("运行配置候选基线已过期")
        await self._ensure_state()
        statement = (
            update(RuntimeConfigState)
            .where(
                RuntimeConfigState.id == 1,
                RuntimeConfigState.revision == expected_revision,
            )
            .values(
                previous_version_id=RuntimeConfigState.active_version_id,
                active_version_id=version_id,
                revision=RuntimeConfigState.revision + 1,
                updated_at=func.now(),
            )
            .returning(RuntimeConfigState)
            .execution_options(populate_existing=True)
        )
        state = cast(RuntimeConfigState | None, await self._session.scalar(statement))
        if state is None:
            raise RuntimeConfigConflictError("运行配置已由其他请求更新")
        await self._session.execute(
            update(RuntimeConfigVersion)
            .where(RuntimeConfigVersion.id == version_id)
            .values(activated_at=func.now())
        )
        self._session.expunge(state)
        return state

    async def rollback(
        self,
        *,
        expected_revision: int,
        expected_previous_version_id: int,
    ) -> RuntimeConfigState:
        """用同一 CAS 规则交换 active 与 previous，支持安全再次回退。"""
        await self._ensure_state()
        statement = (
            update(RuntimeConfigState)
            .where(
                RuntimeConfigState.id == 1,
                RuntimeConfigState.revision == expected_revision,
                RuntimeConfigState.previous_version_id == expected_previous_version_id,
            )
            .values(
                active_version_id=RuntimeConfigState.previous_version_id,
                previous_version_id=RuntimeConfigState.active_version_id,
                revision=RuntimeConfigState.revision + 1,
                updated_at=func.now(),
            )
            .returning(RuntimeConfigState)
            .execution_options(populate_existing=True)
        )
        state = cast(RuntimeConfigState | None, await self._session.scalar(statement))
        if state is not None:
            self._session.expunge(state)
            return state
        current = await self._session.get(RuntimeConfigState, 1, populate_existing=True)
        if current is None or current.revision != expected_revision:
            raise RuntimeConfigConflictError("运行配置已由其他请求更新")
        raise RuntimeConfigRollbackError("当前没有可回滚的上一版本")

    async def prune(self, *, keep_latest: int = 20) -> int:
        """清理旧版本，但始终保留 active、previous 和指定数量的最新记录。"""
        if not 1 <= keep_latest <= 100:
            raise ValueError("配置版本保留数量无效")
        state = await self.get_state()
        newest = list(
            await self._session.scalars(
                select(RuntimeConfigVersion.id)
                .order_by(RuntimeConfigVersion.id.desc())
                .limit(keep_latest)
            )
        )
        protected = {
            version_id
            for version_id in (
                state.active_version_id,
                state.previous_version_id,
                *newest,
            )
            if version_id is not None
        }
        statement = delete(RuntimeConfigVersion)
        if protected:
            statement = statement.where(RuntimeConfigVersion.id.not_in(protected))
        result = await self._session.execute(statement)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def add_audit(
        self,
        *,
        actor_id: int,
        action: str,
        version_id: int | None,
        fields: Sequence[str],
        result: str,
        error_code: str | None,
    ) -> None:
        """只记录字段名、版本、错误码和结果，拒绝任意正文进入审计。"""
        if result not in self._AUDIT_RESULT_VALUES:
            raise ValueError("配置审计结果无效")
        safe_fields = sorted(set(fields))
        if any(self._AUDIT_FIELD_PATTERN.fullmatch(field) is None for field in safe_fields):
            raise ValueError("配置审计字段名无效")
        if error_code is not None and self._AUDIT_ERROR_PATTERN.fullmatch(error_code) is None:
            raise ValueError("配置审计错误码无效")
        details: dict[str, object] = {
            "version_id": version_id,
            "fields": safe_fields,
            "result": result,
        }
        if error_code is not None:
            details["error_code"] = error_code
        self._session.add(
            AuditLog(
                actor_employee_id=actor_id,
                action=action,
                target_type="runtime_config",
                target_id=str(version_id or "environment"),
                details=details,
            )
        )
        await self._session.flush()

    async def _ensure_state(self) -> None:
        """以方言原生 upsert 创建单例状态，避免并发首次访问竞态。"""
        values = {"id": 1, "revision": 0}
        dialect = self._session.get_bind().dialect.name
        statement: Any
        if dialect == "sqlite":
            statement = sqlite_insert(RuntimeConfigState).values(**values)
        elif dialect == "postgresql":
            statement = postgresql_insert(RuntimeConfigState).values(**values)
        else:
            raise RuntimeError(f"不支持的运行配置数据库方言: {dialect}")
        await self._session.execute(
            statement.on_conflict_do_nothing(index_elements=[RuntimeConfigState.id])
        )
        await self._session.flush()

    async def _require_version(self, version_id: int) -> RuntimeConfigVersion:
        """验证激活目标存在，防止把状态指向无效编号。"""
        version = await self.get_version(version_id)
        if version is None:
            raise LookupError("运行配置版本不存在")
        return version

    @classmethod
    def _validate_test_results(
        cls,
        test_results: dict[str, object],
        *,
        succeeded: bool,
    ) -> dict[str, object]:
        """只允许布尔结果和稳定错误码进入候选元数据。"""
        if set(test_results) - {"succeeded", "error_code"}:
            raise ValueError("配置测试结果字段无效")
        if test_results.get("succeeded") is not succeeded:
            raise ValueError("配置测试结果状态无效")
        error_code = test_results.get("error_code")
        if error_code is not None and (
            not isinstance(error_code, str)
            or cls._AUDIT_ERROR_PATTERN.fullmatch(error_code) is None
        ):
            raise ValueError("配置测试错误码无效")
        result: dict[str, object] = {"succeeded": succeeded}
        if error_code is not None:
            result["error_code"] = error_code
        return result
