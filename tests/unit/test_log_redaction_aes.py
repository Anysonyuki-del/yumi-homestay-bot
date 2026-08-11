"""覆盖日志脱敏对企业微信回调 AES 密钥字段的处理。"""

import logging

from homestay_bot.logging import configure_logging_redaction


def _emit(caplog, **extra: object) -> str:
    """在安装脱敏后输出一条带业务 extra 的日志并返回渲染文本。"""
    configure_logging_redaction()
    logger = logging.getLogger("homestay_bot.test.aes")
    with caplog.at_level(logging.INFO, logger="homestay_bot.test.aes"):
        logger.info("配置字段快照", extra=extra)
    record = caplog.records[-1]
    return str(getattr(record, next(iter(extra)), ""))


def test_encoding_aes_key_is_redacted(caplog) -> None:
    """`wecom_encoding_aes_key` 属于凭据，必须与 secret/token 同等脱敏。"""
    value = _emit(caplog, wecom_encoding_aes_key="A" * 43)

    assert "A" * 43 not in value
    assert value == "[REDACTED]"


def test_plain_aes_key_is_redacted(caplog) -> None:
    """裸 `aes_key` 命名同样不得原文进入日志。"""
    value = _emit(caplog, aes_key="B" * 43)

    assert value == "[REDACTED]"


def test_business_dedupe_key_is_not_redacted(caplog) -> None:
    """业务幂等键不是凭据，脱敏不得扩大到普通 key 字段。"""
    value = _emit(caplog, dedupe_key="turnover-101-2026-08-12")

    assert value == "turnover-101-2026-08-12"
