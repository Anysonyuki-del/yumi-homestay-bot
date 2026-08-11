"""编排运行配置合并、无业务写入测试、激活、回滚和安全审计。"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Protocol

from homestay_bot.domain.runtime_config import (
    RuntimeConfigSnapshot,
    RuntimeConfigView,
)
from homestay_bot.repositories.runtime_config import RuntimeConfigConflictError
from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher


@dataclass(frozen=True, slots=True)
class UpdateRuntimeConfig:
    """表示页面明确提交的配置字段；None 或空白文本均保留旧值。"""

    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    hostex_access_token: str | None = None
    hostex_webhook_secret_token: str | None = None
    hostex_reconcile_interval_seconds: float | None = None
    wecom_corp_id: str | None = None
    wecom_kf_secret: str | None = None
    wecom_callback_token: str | None = None
    wecom_encoding_aes_key: str | None = None
    wecom_agent_id: int | None = None
    wecom_agent_secret: str | None = None
    wecom_contact_secret: str | None = None
    wecom_duty_userids: str | None = None
    wecom_poll_interval_seconds: float | None = None
    clear_wecom_contact_secret: bool = False

    @classmethod
    def from_snapshot(cls, snapshot: RuntimeConfigSnapshot) -> "UpdateRuntimeConfig":
        """把完整环境快照转换为首次无环境时可提交的更新命令。"""
        return cls(
            deepseek_api_key=snapshot.deepseek_api_key,
            deepseek_base_url=snapshot.deepseek_base_url,
            deepseek_model=snapshot.deepseek_model,
            hostex_access_token=snapshot.hostex_access_token,
            hostex_webhook_secret_token=snapshot.hostex_webhook_secret_token,
            hostex_reconcile_interval_seconds=(snapshot.hostex_reconcile_interval_seconds),
            wecom_corp_id=snapshot.wecom_corp_id,
            wecom_kf_secret=snapshot.wecom_kf_secret,
            wecom_callback_token=snapshot.wecom_callback_token,
            wecom_encoding_aes_key=snapshot.wecom_encoding_aes_key,
            wecom_agent_id=snapshot.wecom_agent_id,
            wecom_agent_secret=snapshot.wecom_agent_secret,
            wecom_contact_secret=snapshot.wecom_contact_secret,
            wecom_duty_userids=snapshot.wecom_duty_userids,
            wecom_poll_interval_seconds=snapshot.wecom_poll_interval_seconds,
        )

    def normalized_updates(self) -> dict[str, object | None]:
        """把空白文本转换为保留语义，并保持数字字段原值。"""
        updates: dict[str, object | None] = {}
        for field in fields(self):
            if field.name == "clear_wecom_contact_secret":
                continue
            value = getattr(self, field.name)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    updates[field.name] = stripped
            elif value is not None:
                updates[field.name] = value
        if self.clear_wecom_contact_secret:
            if "wecom_contact_secret" in updates:
                raise ValueError("不能同时填写并清除企业微信 Contact Secret")
            updates["wecom_contact_secret"] = None
        return updates

    def changed_fields(self) -> tuple[str, ...]:
        """只返回明确提供的安全字段名，供审计记录。"""
        return tuple(sorted(self.normalized_updates()))


@dataclass(frozen=True, slots=True)
class RuntimeConfigCheckTestResult:
    """保存供应商内部单个只读探针的安全状态。"""

    check: str
    succeeded: bool
    error_code: str | None = None
    verification: str | None = None

    def to_safe_dict(self) -> dict[str, object]:
        """仅序列化布尔状态、稳定错误码和本地校验标记。"""
        result: dict[str, object] = {"succeeded": self.succeeded}
        if self.error_code is not None:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.error_code):
                raise ValueError("配置测试细项错误码无效")
            result["error_code"] = self.error_code
        if self.verification is not None:
            if self.verification != "local_only" or self.check != "callback":
                raise ValueError("配置测试细项校验方式无效")
            result["verification"] = self.verification
        return result


@dataclass(frozen=True, slots=True)
class RuntimeConfigProviderTestResult:
    """保存单一供应商的安全状态，不携带响应正文、URL 或凭据。"""

    provider: str
    succeeded: bool
    error_code: str | None = None
    callback_verification: str | None = None
    checks: tuple[RuntimeConfigCheckTestResult, ...] = ()

    def to_safe_dict(self) -> dict[str, object]:
        """把单项结果限制为持久化白名单字段。"""
        if self.provider not in {"deepseek", "hostex", "wecom"}:
            raise ValueError("配置测试供应商无效")
        if self.error_code is not None and not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", self.error_code
        ):
            raise ValueError("配置测试错误码无效")
        if self.callback_verification not in {None, "local_only"}:
            raise ValueError("企业微信回调校验状态无效")
        if self.provider != "wecom" and self.callback_verification is not None:
            raise ValueError("回调校验状态只能属于企业微信")
        result: dict[str, object] = {"succeeded": self.succeeded}
        if self.error_code is not None:
            result["error_code"] = self.error_code
        if self.callback_verification is not None:
            result["callback_verification"] = self.callback_verification
        if self.checks:
            allowed_checks = {
                "deepseek": {"openai", "anthropic"},
                "hostex": {"properties"},
                "wecom": {"kf", "agent", "contact", "callback"},
            }[self.provider]
            check_results: dict[str, object] = {}
            for check in self.checks:
                if check.check not in allowed_checks or check.check in check_results:
                    raise ValueError("配置测试细项无效或重复")
                check_results[check.check] = check.to_safe_dict()
            result["checks"] = check_results
        return result


@dataclass(frozen=True, slots=True)
class RuntimeConfigTestResult:
    """候选连接测试的安全汇总，不携带远端响应正文。"""

    succeeded: bool
    error_code: str | None = None
    providers: tuple[RuntimeConfigProviderTestResult, ...] = ()

    def to_safe_dict(self) -> dict[str, object]:
        """返回只含布尔状态和稳定错误码的候选元数据。"""
        result: dict[str, object] = {"succeeded": self.succeeded}
        if self.error_code is not None:
            result["error_code"] = self.error_code
        if self.providers:
            provider_results: dict[str, object] = {}
            for provider in self.providers:
                if provider.provider in provider_results:
                    raise ValueError("配置测试供应商重复")
                provider_results[provider.provider] = provider.to_safe_dict()
            result["providers"] = provider_results
        return result


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """向路由返回版本、修订号和脱敏视图。"""

    version_id: int
    revision: int
    view: RuntimeConfigView


@dataclass(frozen=True, slots=True)
class RuntimeConfigVersionView:
    """供版本历史页面使用的不可解密安全投影。"""

    version_id: int
    created_at: datetime | None
    created_by_label: str
    status: str
    failure_code: str | None
    is_active: bool
    is_previous: bool
    masked_summary: dict[str, object]
    provider_results: dict[str, dict[str, object]]


def safe_provider_results(payload: object) -> dict[str, dict[str, object]]:
    """从历史 JSON 提取固定三方安全状态，畸形旧数据一律忽略。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
        return {}
    providers = payload["providers"]
    safe: dict[str, dict[str, object]] = {}
    for name in ("deepseek", "hostex", "wecom"):
        item = providers.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("succeeded"), bool):
            continue
        safe_item: dict[str, object] = {"succeeded": item["succeeded"]}
        error_code = item.get("error_code")
        if isinstance(error_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
            safe_item["error_code"] = error_code
        if name == "wecom" and item.get("callback_verification") == "local_only":
            safe_item["callback_verification"] = "local_only"
        raw_checks = item.get("checks")
        if isinstance(raw_checks, dict):
            ordered_checks = {
                "deepseek": ("openai", "anthropic"),
                "hostex": ("properties",),
                "wecom": ("kf", "agent", "contact", "callback"),
            }[name]
            safe_checks: dict[str, object] = {}
            for check_name in ordered_checks:
                check = raw_checks.get(check_name)
                if not isinstance(check, dict) or not isinstance(
                    check.get("succeeded"), bool
                ):
                    continue
                safe_check: dict[str, object] = {"succeeded": check["succeeded"]}
                check_error = check.get("error_code")
                if isinstance(check_error, str) and re.fullmatch(
                    r"[a-z][a-z0-9_]{0,63}", check_error
                ):
                    safe_check["error_code"] = check_error
                if check_name == "callback" and check.get("verification") == "local_only":
                    safe_check["verification"] = "local_only"
                safe_checks[check_name] = safe_check
            if safe_checks:
                safe_item["checks"] = safe_checks
        safe[name] = safe_item
    return safe


@dataclass(frozen=True, slots=True)
class RuntimeConfigPage:
    """汇总设置页所需的脱敏配置来源和两个 CAS 指针。"""

    view: RuntimeConfigView
    revision: int
    active_version_id: int | None
    previous_version_id: int | None
    source: str
    writable: bool = True


class RuntimeConfigTestError(RuntimeError):
    """表示候选连接测试未通过，只公开稳定错误码。"""

    def __init__(self, error_code: str) -> None:
        """保存经过白名单格式归一化的错误码。"""
        self.error_code = error_code
        super().__init__("候选配置测试未通过")


class RuntimeConfigUnavailableError(RuntimeError):
    """表示数据库和环境均没有可作为编辑基线的完整外部配置。"""


@dataclass(frozen=True, slots=True)
class RuntimeConfigBaseline:
    """绑定一次读取获得的 revision、active 版本和完整快照。"""

    revision: int
    active_version_id: int | None
    snapshot: RuntimeConfigSnapshot | None


class RuntimeConfigRepositoryPort(Protocol):
    """定义核心服务所需的版本仓储接口。"""

    async def get_state(self) -> Any:
        """返回当前激活状态。"""

    async def get_active_version(self) -> Any | None:
        """返回当前激活版本。"""

    async def get_activation_context(self) -> tuple[Any, Any | None]:
        """在同一查询中返回状态与匹配的 active 版本。"""

    async def get_version(self, version_id: int) -> Any | None:
        """按编号返回版本。"""

    async def list_versions(self, *, limit: int = 20) -> list[Any]:
        """返回有界版本历史。"""

    async def create_candidate(
        self,
        encrypted_payload: bytes,
        masked_summary: dict[str, object],
        actor_id: int,
        *,
        based_on_version_id: int | None,
        based_on_revision: int,
    ) -> Any:
        """创建不可变候选。"""

    async def mark_test_passed(
        self,
        version_id: int,
        test_results: dict[str, object],
    ) -> None:
        """记录候选测试通过。"""

    async def mark_test_failed(
        self,
        version_id: int,
        test_results: dict[str, object],
        *,
        failure_code: str,
    ) -> None:
        """记录候选测试失败。"""

    async def mark_activation_conflict(self, version_id: int) -> None:
        """记录候选因 revision 过期未激活。"""

    async def activate(self, version_id: int, expected_revision: int) -> Any:
        """按修订号激活候选。"""

    async def rollback(
        self,
        *,
        expected_revision: int,
        expected_previous_version_id: int,
    ) -> Any:
        """按修订号交换当前和上一版本。"""

    async def prune(self, *, keep_latest: int = 20) -> int:
        """安全清理旧版本。"""

    async def add_audit(self, **fields: object) -> None:
        """写入不含配置值的审计。"""


class AdminReverifyPort(Protocol):
    """定义敏感配置操作所需的二次密码复核。"""

    async def reverify_at_version(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> None:
        """校验唯一管理员当前密码及页面会话版本。"""


class RuntimeConfigTesterPort(Protocol):
    """定义可注入的无业务写入候选测试端口。"""

    async def test(self, snapshot: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
        """测试候选并只返回稳定结果和错误码。"""


class RuntimeConfigService:
    """保证配置只有在复核、测试和 CAS 激活全部成功后才生效。"""

    _ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

    def __init__(
        self,
        *,
        repository: RuntimeConfigRepositoryPort,
        cipher: RuntimeConfigCipher,
        auth: AdminReverifyPort,
        tester: RuntimeConfigTesterPort,
        environment_snapshot: RuntimeConfigSnapshot | None,
        retention: int = 20,
        before_test: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """注入存储、独立密文服务、密码复核和候选测试端口。"""
        if not 2 <= retention <= 100:
            raise ValueError("配置版本保留数量无效")
        self._repository = repository
        self._cipher = cipher
        self._auth = auth
        self._tester = tester
        self._environment_snapshot = environment_snapshot
        self._retention = retention
        self._before_test = before_test

    async def load_active_or_environment(self) -> RuntimeConfigSnapshot:
        """数据库无激活版本时回退环境；一旦激活则数据库快照优先。"""
        version = await self._repository.get_active_version()
        if version is None:
            if self._environment_snapshot is None:
                raise RuntimeConfigUnavailableError("尚未配置外部服务")
            return self._environment_snapshot
        return self._cipher.decrypt(bytes(version.encrypted_payload))

    async def current_view(self) -> tuple[RuntimeConfigView, int, bool]:
        """返回当前脱敏视图、修订号及是否来自数据库。"""
        state = await self._repository.get_state()
        snapshot = await self.load_active_or_environment()
        return snapshot.masked_view(), int(state.revision), state.active_version_id is not None

    async def page_data(self) -> RuntimeConfigPage:
        """返回设置页脱敏数据；完全未配置时也提供可修复的空白投影。"""
        state, version = await self._repository.get_activation_context()
        if version is not None:
            snapshot = self._cipher.decrypt(bytes(version.encrypted_payload))
            view = snapshot.masked_view()
            source = "database"
        elif self._environment_snapshot is not None:
            view = self._environment_snapshot.masked_view()
            source = "environment"
        else:
            view = RuntimeConfigView.empty()
            source = "unconfigured"
        return RuntimeConfigPage(
            view=view,
            revision=int(state.revision),
            active_version_id=state.active_version_id,
            previous_version_id=state.previous_version_id,
            source=source,
        )

    async def list_version_views(self, *, limit: int = 20) -> list[RuntimeConfigVersionView]:
        """返回只使用持久化掩码摘要的历史列表，绝不解密每个旧版本。"""
        state = await self._repository.get_state()
        versions = await self._repository.list_versions(limit=limit)
        return [
            RuntimeConfigVersionView(
                version_id=int(version.id),
                created_at=getattr(version, "created_at", None),
                created_by_label=(
                    "YuMi 管理员" if getattr(version, "created_by", None) is not None else "系统"
                ),
                status=(
                    version.status.value
                    if hasattr(version.status, "value")
                    else str(version.status)
                ),
                failure_code=getattr(version, "failure_code", None),
                is_active=version.id == state.active_version_id,
                is_previous=version.id == state.previous_version_id,
                masked_summary=dict(version.masked_summary),
                provider_results=safe_provider_results(version.test_results),
            )
            for version in versions
        ]

    async def create_and_test(
        self,
        command: UpdateRuntimeConfig,
        actor_id: int,
        admin_id: int,
        password: str,
        expected_session_version: int,
        expected_revision: int,
    ) -> ActivationResult:
        """复核密码、测试完整候选，再用乐观锁创建并激活单密文版本。"""
        await self._auth.reverify_at_version(
            admin_id,
            password,
            expected_session_version,
        )
        baseline = await self._read_baseline()
        if baseline.revision != expected_revision:
            raise RuntimeConfigConflictError("设置页面版本已过期")
        updates = command.normalized_updates()
        if baseline.snapshot is None:
            payload = dict(updates)
            payload["schema_version"] = 1
            candidate = RuntimeConfigSnapshot.from_dict(payload)
        else:
            candidate = baseline.snapshot.merged(updates)
        changed_fields = command.changed_fields()
        version = await self._repository.create_candidate(
            self._cipher.encrypt(candidate),
            candidate.masked_view().to_dict(),
            actor_id,
            based_on_version_id=baseline.active_version_id,
            based_on_revision=baseline.revision,
        )
        if self._before_test is not None:
            await self._before_test()
        test_result = await self._tester.test(candidate)
        if not test_result.succeeded:
            error_code = self._safe_error_code(test_result.error_code)
            safe_result = RuntimeConfigTestResult(
                succeeded=False,
                error_code=error_code,
                providers=test_result.providers,
            ).to_safe_dict()
            await self._repository.mark_test_failed(
                int(version.id),
                safe_result,
                failure_code=error_code,
            )
            await self._repository.add_audit(
                actor_id=actor_id,
                action="runtime_config.test",
                version_id=int(version.id),
                fields=changed_fields,
                result="failed",
                error_code=error_code,
            )
            raise RuntimeConfigTestError(error_code)

        await self._repository.mark_test_passed(int(version.id), test_result.to_safe_dict())
        try:
            activated = await self._repository.activate(
                int(version.id),
                expected_revision=baseline.revision,
            )
        except RuntimeConfigConflictError:
            await self._repository.mark_activation_conflict(int(version.id))
            await self._repository.add_audit(
                actor_id=actor_id,
                action="runtime_config.activate",
                version_id=int(version.id),
                fields=changed_fields,
                result="conflict",
                error_code="revision_conflict",
            )
            raise
        await self._repository.add_audit(
            actor_id=actor_id,
            action="runtime_config.activate",
            version_id=int(version.id),
            fields=changed_fields,
            result="ok",
            error_code=None,
        )
        await self._repository.prune(keep_latest=self._retention)
        return ActivationResult(
            version_id=int(version.id),
            revision=int(activated.revision),
            view=candidate.masked_view(),
        )

    async def rollback(
        self,
        actor_id: int,
        admin_id: int,
        password: str,
        expected_session_version: int,
        expected_revision: int,
        expected_previous_version_id: int,
    ) -> ActivationResult:
        """复核后恢复上一已验证版本，并按 revision 原子交换两个有效指针。"""
        await self._auth.reverify_at_version(
            admin_id,
            password,
            expected_session_version,
        )
        state = await self._repository.get_state()
        if (
            int(state.revision) != expected_revision
            or state.previous_version_id != expected_previous_version_id
        ):
            raise RuntimeConfigConflictError("设置页面回滚目标已过期")
        previous_id = state.previous_version_id
        if previous_id is None:
            raise LookupError("当前没有可回滚的上一配置")
        previous = await self._repository.get_version(int(previous_id))
        if previous is None:
            raise LookupError("上一配置版本不存在")
        snapshot = self._cipher.decrypt(bytes(previous.encrypted_payload))
        rolled_back = await self._repository.rollback(
            expected_revision=expected_revision,
            expected_previous_version_id=expected_previous_version_id,
        )
        await self._repository.add_audit(
            actor_id=actor_id,
            action="runtime_config.rollback",
            version_id=int(previous_id),
            fields=(),
            result="ok",
            error_code=None,
        )
        return ActivationResult(
            version_id=int(previous_id),
            revision=int(rolled_back.revision),
            view=snapshot.masked_view(),
        )

    async def _read_baseline(self) -> RuntimeConfigBaseline:
        """一次读取匹配的状态与 active 密文，固定候选测试期间的基线。"""
        state, version = await self._repository.get_activation_context()
        snapshot = (
            self._cipher.decrypt(bytes(version.encrypted_payload))
            if version is not None
            else self._environment_snapshot
        )
        return RuntimeConfigBaseline(
            revision=int(state.revision),
            active_version_id=state.active_version_id,
            snapshot=snapshot,
        )

    @classmethod
    def _safe_error_code(cls, value: str | None) -> str:
        """把测试器返回值压缩为可安全审计和展示的稳定错误码。"""
        if value is None or cls._ERROR_CODE_PATTERN.fullmatch(value) is None:
            return "integration_test_failed"
        return value
