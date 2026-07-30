import base64
import hashlib
import hmac
import re

from cryptography.fernet import Fernet


class SensitiveDataCipher:
    """使用独立数据密钥加密敏感字段并生成不可逆匹配指纹。"""

    _PHONE_CONTEXT = b"homestay-bot:phone-fingerprint:v1\0"
    _PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

    def __init__(self, encryption_key: str) -> None:
        """校验 Fernet 密钥并派生仅用于手机号匹配的 HMAC 密钥。"""
        encoded_key = encryption_key.encode("ascii")
        self._fernet = Fernet(encoded_key)
        self._raw_key = base64.urlsafe_b64decode(encoded_key)
        self._fingerprint_key = hmac.digest(
            self._raw_key,
            b"homestay-bot:fingerprint-key:v1",
            hashlib.sha256,
        )

    def encrypt(self, value: str, *, purpose: str | None = None) -> bytes:
        """加密敏感文本，并可用独立派生密钥隔离新字段用途。"""
        cipher = self._fernet if purpose is None else self._purpose_cipher(purpose)
        return cipher.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes, *, purpose: str | None = None) -> str:
        """仅在密钥及可选用途均匹配时把密文还原为文本。"""
        cipher = self._fernet if purpose is None else self._purpose_cipher(purpose)
        return cipher.decrypt(value).decode("utf-8")

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

    def _purpose_cipher(self, purpose: str) -> Fernet:
        """用主数据密钥为指定用途派生独立 Fernet 子密钥。"""
        if self._PURPOSE_PATTERN.fullmatch(purpose) is None:
            raise ValueError("敏感数据用途名称无效")
        context = f"homestay-bot:{purpose}:fernet-key:v1".encode()
        derived_key = hmac.digest(
            self._raw_key,
            context,
            hashlib.sha256,
        )
        return Fernet(base64.urlsafe_b64encode(derived_key))
