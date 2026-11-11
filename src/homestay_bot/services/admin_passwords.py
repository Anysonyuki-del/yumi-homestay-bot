from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError

ADMIN_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
MAX_ADMIN_PASSWORD_LENGTH = 128


def validate_admin_password_hash(password_hash: str) -> None:
    """只接受结构合法、算法和参数均匹配当前安全基线的 Argon2id 哈希。"""
    try:
        parameters = extract_parameters(password_hash)
        needs_rehash = ADMIN_PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError as exc:
        raise ValueError("管理员引导密码必须是合法 Argon2id 哈希") from exc
    if parameters.type is not Type.ID:
        raise ValueError("管理员引导密码必须使用 Argon2id")
    if needs_rehash:
        raise ValueError("管理员引导密码哈希必须使用当前安全参数")


def validate_new_admin_password(password: str) -> None:
    """允许管理员自定密码长度，但拒绝空白和异常超长输入。"""
    if not password.strip():
        raise ValueError("新密码不能为空或全为空白")
    if len(password) > MAX_ADMIN_PASSWORD_LENGTH:
        raise ValueError("新密码不能超过 128 个字符")
