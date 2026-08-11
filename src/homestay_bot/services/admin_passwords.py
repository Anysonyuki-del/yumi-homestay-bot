from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ADMIN_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
MIN_ADMIN_PASSWORD_LENGTH = 12
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


def hash_admin_password(password: str) -> str:
    """使用全局统一参数生成 Argon2id 管理员密码哈希。"""
    return ADMIN_PASSWORD_HASHER.hash(password)


def verify_admin_password(password_hash: str, password: str) -> bool:
    """把密码不匹配或非法编码统一折叠为布尔失败。"""
    try:
        return ADMIN_PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def validate_new_admin_password(password: str) -> None:
    """要求新密码非空白且长度为 12 至 128 个字符。"""
    if not password.strip():
        raise ValueError("新密码不能为空或全为空白")
    if not MIN_ADMIN_PASSWORD_LENGTH <= len(password) <= MAX_ADMIN_PASSWORD_LENGTH:
        raise ValueError("新密码长度必须为 12 至 128 个字符")
