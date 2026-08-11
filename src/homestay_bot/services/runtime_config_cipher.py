"""使用独立配置主密钥加密完整运行配置快照。"""

import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet

from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot


class RuntimeConfigPayloadError(ValueError):
    """表示密文大小、信封或快照结构无效，不携带原始正文。"""


class RuntimeConfigCipher:
    """以固定用途派生子密钥，隔离配置密文与其他业务密文。"""

    _CONTEXT = b"homestay-bot:runtime-config-snapshot:fernet-key:v1"
    _PURPOSE = "runtime_config_snapshot"
    _SCHEMA_VERSION = 1
    _MAX_PLAINTEXT_BYTES = 65_536
    _MAX_ENCRYPTED_BYTES = 131_072

    def __init__(self, encryption_key: str) -> None:
        """校验独立 CONFIG_ENCRYPTION_KEY 并派生配置专用 Fernet 密钥。"""
        encoded_key = encryption_key.encode("ascii")
        raw_key = base64.urlsafe_b64decode(encoded_key)
        if len(raw_key) != 32:
            raise ValueError("配置加密主密钥无效")
        derived_key = hmac.digest(raw_key, self._CONTEXT, hashlib.sha256)
        self._fernet = Fernet(base64.urlsafe_b64encode(derived_key))

    def encrypt(self, snapshot: RuntimeConfigSnapshot) -> bytes:
        """校验并把整份快照序列化为单个随机认证密文。"""
        snapshot.validate()
        envelope = {
            "purpose": self._PURPOSE,
            "schema_version": self._SCHEMA_VERSION,
            "snapshot": snapshot.to_dict(),
        }
        plaintext = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(plaintext) > self._MAX_PLAINTEXT_BYTES:
            raise RuntimeConfigPayloadError("运行配置快照过大")
        return self._fernet.encrypt(plaintext)

    def decrypt(self, encrypted_payload: bytes) -> RuntimeConfigSnapshot:
        """认证解密整份快照，并严格恢复固定字段结构。"""
        if len(encrypted_payload) > self._MAX_ENCRYPTED_BYTES:
            raise RuntimeConfigPayloadError("运行配置密文过大")
        plaintext = self._fernet.decrypt(encrypted_payload)
        if len(plaintext) > self._MAX_PLAINTEXT_BYTES:
            raise RuntimeConfigPayloadError("运行配置快照过大")
        try:
            envelope = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeConfigPayloadError("运行配置快照结构无效") from error
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"purpose", "schema_version", "snapshot"}
            or envelope.get("purpose") != self._PURPOSE
            or envelope.get("schema_version") != self._SCHEMA_VERSION
            or not isinstance(envelope.get("snapshot"), dict)
        ):
            raise RuntimeConfigPayloadError("运行配置快照结构无效")
        try:
            return RuntimeConfigSnapshot.from_dict(envelope["snapshot"])
        except (TypeError, ValueError) as error:
            raise RuntimeConfigPayloadError("运行配置快照字段无效") from error
