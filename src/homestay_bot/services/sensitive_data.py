import base64
import hashlib
import hmac

from cryptography.fernet import Fernet


class SensitiveDataCipher:
    """使用独立数据密钥加密敏感字段并生成不可逆匹配指纹。"""

    _PHONE_CONTEXT = b"homestay-bot:phone-fingerprint:v1\0"

    def __init__(self, encryption_key: str) -> None:
        """校验 Fernet 密钥并派生仅用于手机号匹配的 HMAC 密钥。"""
        encoded_key = encryption_key.encode("ascii")
        self._fernet = Fernet(encoded_key)
        raw_key = base64.urlsafe_b64decode(encoded_key)
        self._fingerprint_key = hmac.digest(
            raw_key,
            b"homestay-bot:fingerprint-key:v1",
            hashlib.sha256,
        )

    def encrypt(self, value: str) -> bytes:
        """把敏感文本加密为可安全落库的二进制密文。"""
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes) -> str:
        """仅在授权业务流程中把密文还原为文本。"""
        return self._fernet.decrypt(value).decode("utf-8")

    def fingerprint(self, phone: str) -> str:
        """规范化手机号并返回带用途隔离的稳定 HMAC 指纹。"""
        normalized = "".join(
            character for character in phone.strip() if character not in " -()"
        )
        payload = self._PHONE_CONTEXT + normalized.encode("utf-8")
        return hmac.new(
            self._fingerprint_key,
            payload,
            hashlib.sha256,
        ).hexdigest()
