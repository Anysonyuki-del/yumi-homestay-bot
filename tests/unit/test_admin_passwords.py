import pytest
from argon2 import PasswordHasher, Type

from homestay_bot.services.admin_passwords import validate_admin_password_hash


def test_validator_rejects_weak_argon2id_parameters() -> None:
    """结构合法但参数弱于当前基线的 Argon2id 哈希不能用于引导。"""
    weak_hash = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        type=Type.ID,
    ).hash("bootstrap-password")

    with pytest.raises(ValueError, match="安全参数"):
        validate_admin_password_hash(weak_hash)


def test_validator_rejects_abnormally_large_parameters_without_hashing() -> None:
    """异常巨大参数只能被解析并拒绝，不能触发对应内存分配。"""
    huge_hash = "$argon2id$v=19$m=4294967295,t=999999,p=255$YWJj$ZGVm"

    with pytest.raises(ValueError, match="安全参数"):
        validate_admin_password_hash(huge_hash)
