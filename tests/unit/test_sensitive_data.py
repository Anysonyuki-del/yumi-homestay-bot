import pytest
from cryptography.fernet import Fernet, InvalidToken

from homestay_bot.services.sensitive_data import SensitiveDataCipher


def test_sensitive_data_cipher_encrypts_without_plaintext() -> None:
    """手机号密文不得包含明文，并且只能由原密钥解密。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    ciphertext = cipher.encrypt("13800138000")

    assert b"13800138000" not in ciphertext
    assert cipher.decrypt(ciphertext) == "13800138000"


def test_phone_fingerprint_is_stable_and_context_separated() -> None:
    """相同手机号应产生稳定指纹，不同手机号不能产生相同结果。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    first = cipher.fingerprint("13800138000")
    repeated = cipher.fingerprint("13800138000")
    different = cipher.fingerprint("13900139000")

    assert first == repeated
    assert first != different
    assert len(first) == 64


def test_wrong_key_cannot_decrypt_sensitive_data() -> None:
    """更换数据密钥后不得静默解出错误内容。"""
    original = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    other = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    ciphertext = original.encrypt("13800138000")

    with pytest.raises(InvalidToken):
        other.decrypt(ciphertext)


def test_room_password_uses_purpose_separated_encryption() -> None:
    """房间密码密文必须隔离用途，不能作为其他敏感字段解密。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    ciphertext = cipher.encrypt("839201", purpose="room_password")

    assert b"839201" not in ciphertext
    assert cipher.decrypt(ciphertext, purpose="room_password") == "839201"
    with pytest.raises(InvalidToken):
        cipher.decrypt(ciphertext, purpose="checkin_guide")
    with pytest.raises(InvalidToken):
        cipher.decrypt(ciphertext)
