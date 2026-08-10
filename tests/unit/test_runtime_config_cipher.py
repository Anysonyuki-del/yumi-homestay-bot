import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def build_snapshot(**overrides: object) -> RuntimeConfigSnapshot:
    """构造包含全部外部集成字段的测试快照。"""
    values: dict[str, object] = {
        "deepseek_api_key": "deepseek-secret-A1B2",
        "deepseek_base_url": "https://api.deepseek.example",
        "deepseek_model": "deepseek-v4-flash",
        "hostex_access_token": "hostex-secret-C3D4",
        "hostex_webhook_secret_token": "hostex-webhook-E5F6",
        "hostex_reconcile_interval_seconds": 900.0,
        "wecom_corp_id": "corp-G7H8",
        "wecom_kf_secret": "kf-secret-I9J0",
        "wecom_callback_token": "callback-K1L2",
        "wecom_encoding_aes_key": "A" * 43,
        "wecom_agent_id": 1000002,
        "wecom_agent_secret": "agent-M3N4",
        "wecom_contact_secret": "contact-O5P6",
        "wecom_duty_userids": "owner",
        "wecom_poll_interval_seconds": 10.0,
    }
    values.update(overrides)
    return RuntimeConfigSnapshot(**values)  # type: ignore[arg-type]


def test_cipher_encrypts_one_complete_snapshot_without_plaintext() -> None:
    """整份快照必须形成一个密文，任何秘密都不能出现在载荷中。"""
    snapshot = build_snapshot()
    cipher = RuntimeConfigCipher(Fernet.generate_key().decode())

    encrypted = cipher.encrypt(snapshot)

    assert cipher.decrypt(encrypted) == snapshot
    for secret in (
        snapshot.deepseek_api_key,
        snapshot.hostex_access_token,
        snapshot.wecom_agent_secret,
    ):
        assert secret.encode() not in encrypted
    assert "secret" not in repr(snapshot).lower()


def test_cipher_rejects_wrong_master_key_and_other_purpose() -> None:
    """配置主密钥错误或密文用途不同都必须拒绝解密。"""
    key = Fernet.generate_key().decode()
    snapshot = build_snapshot()
    encrypted = RuntimeConfigCipher(key).encrypt(snapshot)

    with pytest.raises(InvalidToken):
        RuntimeConfigCipher(Fernet.generate_key().decode()).decrypt(encrypted)

    foreign_payload = SensitiveDataCipher(key).encrypt(
        json.dumps(snapshot.to_dict()),
        purpose="room_password",
    )
    with pytest.raises(InvalidToken):
        RuntimeConfigCipher(key).decrypt(foreign_payload)


def test_masked_view_never_exposes_complete_identity_or_secret() -> None:
    """页面投影只返回配置状态和尾号，不能包含完整凭据或企业身份。"""
    snapshot = build_snapshot()

    view = snapshot.masked_view()
    serialized = json.dumps(view.to_dict(), ensure_ascii=False)

    assert view.deepseek_api_key == "已配置 ····A1B2"
    assert view.wecom_contact_secret == "已配置 ····O5P6"
    assert view.deepseek_base_url == snapshot.deepseek_base_url
    assert view.deepseek_model == snapshot.deepseek_model
    for hidden in (
        snapshot.deepseek_api_key,
        snapshot.hostex_access_token,
        snapshot.wecom_corp_id,
        snapshot.wecom_duty_userids,
    ):
        assert hidden not in serialized


def test_snapshot_round_trip_rejects_unknown_or_missing_fields() -> None:
    """密文 JSON 必须是完整固定结构，避免旧载荷静默丢字段。"""
    payload = build_snapshot().to_dict()
    payload["unexpected"] = "not-allowed"
    with pytest.raises(ValueError):
        RuntimeConfigSnapshot.from_dict(payload)


def test_cipher_requires_versioned_purpose_envelope() -> None:
    """即使由正确子密钥加密，旧式裸快照也不能绕过信封版本检查。"""
    cipher = RuntimeConfigCipher(Fernet.generate_key().decode())
    legacy = cipher._fernet.encrypt(  # noqa: SLF001 - 刻意构造旧格式回归样本
        json.dumps(build_snapshot().to_dict()).encode()
    )

    with pytest.raises(ValueError):
        cipher.decrypt(legacy)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"wecom_agent_id": True},
        {"wecom_poll_interval_seconds": "10"},
        {"deepseek_api_key": "x" * 4097},
        {"deepseek_base_url": "https://example.test/" + "x" * 2048},
    ],
)
def test_snapshot_enforces_schema_exact_types_and_lengths(
    overrides: dict[str, object],
) -> None:
    """密文恢复不能依赖 Python 隐式类型转换或接受无界正文。"""
    payload = build_snapshot().to_dict()
    payload.update(overrides)

    with pytest.raises(ValueError):
        RuntimeConfigSnapshot.from_dict(payload)

    payload = build_snapshot().to_dict()
    del payload["wecom_kf_secret"]
    with pytest.raises(ValueError):
        RuntimeConfigSnapshot.from_dict(payload)
