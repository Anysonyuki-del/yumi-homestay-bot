import base64
import hashlib
import os
import struct

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from homestay_bot.integrations.wecom.callback_crypto import (
    InvalidCallbackSignature,
    WeComCallbackCrypto,
)


def encrypt_fixture(
    xml: bytes,
    *,
    token: str,
    encoding_aes_key: str,
    receive_id: str,
    timestamp: str,
    nonce: str,
) -> tuple[str, str]:
    """独立构造企业微信兼容密文，避免测试复用生产解密逻辑。"""
    key = base64.b64decode(f"{encoding_aes_key}=")
    plaintext = os.urandom(16) + struct.pack("!I", len(xml)) + xml + receive_id.encode()
    padder = PKCS7(256).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    encoded = base64.b64encode(encrypted).decode()
    signature = hashlib.sha1(
        "".join(sorted([token, timestamp, nonce, encoded])).encode()
    ).hexdigest()
    return encoded, signature


def test_callback_crypto_verifies_and_decrypts_message() -> None:
    """有效签名和 CorpID 应解密出原始事件 XML。"""
    token = "callback-token"
    aes_key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
    xml = b"<xml><Event>kf_msg_or_event</Event><Token>sync-token</Token></xml>"
    encrypted, signature = encrypt_fixture(
        xml,
        token=token,
        encoding_aes_key=aes_key,
        receive_id="corp-id",
        timestamp="100",
        nonce="200",
    )
    crypto = WeComCallbackCrypto(token, aes_key, "corp-id")

    decrypted = crypto.decrypt(encrypted, signature, "100", "200")

    assert decrypted == xml


def test_callback_crypto_rejects_invalid_signature() -> None:
    """签名不匹配时不得尝试处理密文。"""
    aes_key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
    crypto = WeComCallbackCrypto("token", aes_key, "corp-id")

    with pytest.raises(InvalidCallbackSignature):
        crypto.decrypt("cipher", "bad-signature", "100", "200")

