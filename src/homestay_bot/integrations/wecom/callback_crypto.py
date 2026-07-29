import base64
import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


class InvalidCallbackSignature(ValueError):
    """表示企业微信回调签名不可信。"""


class InvalidCallbackPayload(ValueError):
    """表示企业微信回调密文或接收方不合法。"""


class WeComCallbackCrypto:
    """验证并解密企业微信 AES 回调。"""

    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        """解析 43 字符 EncodingAESKey 并保存接收方 CorpID。"""
        try:
            key = base64.b64decode(f"{encoding_aes_key}=")
        except ValueError as error:
            raise InvalidCallbackPayload("EncodingAESKey 不是有效 Base64") from error
        if len(key) != 32:
            raise InvalidCallbackPayload("EncodingAESKey 解码后必须为 32 字节")
        self._token = token
        self._key = key
        self._receive_id = receive_id.encode()

    def decrypt(
        self,
        encrypted: str,
        signature: str,
        timestamp: str,
        nonce: str,
    ) -> bytes:
        """先验签再解密，并校验消息尾部的 CorpID。"""
        expected_signature = hashlib.sha1(
            "".join(
                sorted([self._token, timestamp, nonce, encrypted])
            ).encode()
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, signature):
            raise InvalidCallbackSignature("企业微信回调签名不匹配")

        try:
            ciphertext = base64.b64decode(encrypted)
            decryptor = Cipher(
                algorithms.AES(self._key), modes.CBC(self._key[:16])
            ).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = PKCS7(256).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except (ValueError, TypeError) as error:
            raise InvalidCallbackPayload("企业微信回调密文无法解密") from error

        if len(plaintext) < 20:
            raise InvalidCallbackPayload("企业微信回调明文长度不足")
        message_length = struct.unpack("!I", plaintext[16:20])[0]
        message_end = 20 + message_length
        message = plaintext[20:message_end]
        receive_id = plaintext[message_end:]
        if receive_id != self._receive_id:
            raise InvalidCallbackPayload("企业微信回调 CorpID 不匹配")
        return message

