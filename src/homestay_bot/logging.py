import logging
import re
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEY_PATTERN = re.compile(
    # aes_key 覆盖企业微信回调 EncodingAESKey；不放宽到裸 key，避免误伤业务幂等键。
    r"token|secret|password|api[_-]?key|aes[_-]?key|authorization",
    re.IGNORECASE,
)
_TOKEN_IN_TEXT_PATTERN = re.compile(
    r"(?i)(Hostex-Access-Token|Authorization)\s*:\s*"
    # 负向先行断言让已脱敏文本不再匹配，过滤器重复作用时不会叠加括号。
    r"(?!\[REDACTED\])"
    # 与 _SECRET_ASSIGNMENT_PATTERN 一致：方案词后的凭证必须一并消费，
    # 否则 "Authorization: Bearer <token>" 只会遮住 Bearer，令牌明文残留。
    r"(?:(?:Bearer|Basic|Token)\s+)?\S+"
)
_SENSITIVE_QUERY_KEYS = {
    "code",
    # 微信 code2Session 的一次性登录凭证；只有 code 一项时 `&js_code=` 不会命中。
    "js_code",
    "state",
    "msg_signature",
    "signature",
    "echostr",
    "token",
}

# 异常栈和调用栈是多行文本，按 URL 解析会压掉换行并丢失定位信息，因此改为
# 只替换键值对。负向先行断言让过滤器可以重复作用于同一条记录而不叠加括号。
_QUERY_PARAM_IN_TEXT_PATTERN = re.compile(
    r"(?i)([?&])(" + "|".join(sorted(_SENSITIVE_QUERY_KEYS)) + r")="
    r"(?!\[REDACTED\])[^&\s\"'>]*"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    # 键保留 JSON/repr 引号，值中反斜线与下一字符成组，只有真实结束引号才终止。
    r'''(?i)(["']?[\w.-]*(?:token|secret|password|api[_-]?key|aes[_-]?key|authorization)["']?)'''
    r"(\s*[=:]\s*)"
    r"(?!\[REDACTED\])"
    # Bearer、Basic 等方案词与凭证之间有空格，必须一并消费才不会残留正文。
    r"(?:(?:Bearer|Basic|Token)\s+)?"
    r'''(?:"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|[^\s,;)\]}&]+)'''
)

_EXCEPTION_FORMATTER = logging.Formatter()

# LogRecord 的标准字段不属于业务 extra，避免把日志元数据误当成业务内容。
_STANDARD_RECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def _redact_value(key: str, value: Any) -> Any:
    """根据字段名递归脱敏密钥与令牌，不改变可安全审计的请求编号。

    客人手机号不再打码：用户 2026-09-26 决定服务器日志可以记录客人信息（1.41.0）。
    密钥、令牌、密码仍然脱敏，它们泄露会导致服务器被入侵，与客人信息无关。
    """
    if _SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {item_key: _redact_value(str(item_key), item) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    """脱敏任意日志文本里的凭证与敏感查询参数，保留原文结构。

    普通消息、字段值、异常栈与调用栈共用这一个入口。此前普通消息按「含 ? 就整句
    当 URL 解析」处理，查询参数之外的令牌全被跳过，也只认冒号写法，导致
    「Authorization=Bearer x」「Authorization: "Bearer x"」以及与回调 URL 同行的
    令牌原样输出（Codex 审查 P1）。这里只替换键值对，不重新编码整句，中文问句
    与多行栈都保持原样；两种模式都带负向先行断言，重复过滤不叠加。
    """
    redacted = _QUERY_PARAM_IN_TEXT_PATTERN.sub(r"\1\2=[REDACTED]", value)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    return _TOKEN_IN_TEXT_PATTERN.sub(r"\1: [REDACTED]", redacted)


def redact_exception_text(value: str) -> str:
    """脱敏异常栈或调用栈文本，同时保留多行结构、帧信息和异常类型。"""
    return redact_text(value)


def redact_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """返回递归脱敏后的日志字段副本，不修改调用方原始数据。"""
    return {key: _redact_value(key, value) for key, value in fields.items()}


def _redact_url(value: str) -> str:
    """脱敏 Uvicorn 访问日志参数里的 URL 查询参数（OAuth code、state、回调签名）。

    只用于访问日志的五元参数：参数本身就是一条请求路径，按 URL 解析不会误伤正文。
    普通消息一律走 redact_text。
    """
    if "?" not in value:
        return _TOKEN_IN_TEXT_PATTERN.sub(r"\1: [REDACTED]", value)
    split = urlsplit(value)
    query = [
        (key, "[REDACTED]" if key.lower() in _SENSITIVE_QUERY_KEYS else item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query), split.fragment)
    )


class SensitiveDataFilter(logging.Filter):
    """在日志格式化前脱敏结构化字段和访问日志 URL。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """修改消息、参数和 extra 副本并允许记录继续输出。"""
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
        ):
            # Uvicorn 的 AccessFormatter 会再次解包固定五元参数；只在原参数中
            # 脱敏字符串，不能像普通日志一样预渲染后清空 args。
            record.args = tuple(
                _redact_url(item) if isinstance(item, str) else item
                for item in record.args
            )
        elif isinstance(record.args, tuple) and not isinstance(record.msg, (Mapping, list)):
            # 字典/列表消息在下方递归保留结构；引号键现在可匹配，不能先渲染成字符串。
            # 令牌名称通常位于格式字符串而不是参数键中，先渲染整条消息，
            # 再替换并冻结参数，避免子 logger 的 handler 漏掉令牌正文。
            try:
                rendered = record.getMessage()
            except (TypeError, ValueError):
                rendered = None
            if rendered is not None:
                redacted = redact_text(rendered)
                if redacted != rendered:
                    record.msg = redacted
                    record.args = ()
                else:
                    record.args = tuple(
                        redact_text(item) if isinstance(item, str) else item
                        for item in record.args
                    )
        elif isinstance(record.args, dict):
            record.args = redact_log_fields(record.args)

        # logging 允许直接把字典或列表作为 msg；这种写法没有 args，
        # 因而必须在消息对象本身递归处理，不能只依赖 getMessage()。
        if isinstance(record.msg, (Mapping, list)):
            record.msg = _redact_value("", record.msg)

        # extra 字段会直接挂到 LogRecord 上，逐项过滤可以覆盖 token、手机号、
        # 嵌套字典等自定义字段，同时跳过标准日志元数据。
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_RECORD_FIELDS:
                continue
            redacted = _redact_value(key, value)
            if redacted != value:
                setattr(record, key, redacted)

        # exc_text 与 stack_info 属于标准字段，上面的 extra 循环会跳过；没有这段
        # 处理时 traceback 会绕过全部脱敏规则，也正因如此项目此前无法使用
        # logger.exception。渲染后清空 exc_info，确保下游 formatter 只能读到
        # 已脱敏文本，不会从原始异常重新渲染一份未脱敏的栈。
        if record.exc_info is not None and not record.exc_text:
            record.exc_text = _EXCEPTION_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_exception_text(record.exc_text)
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact_exception_text(record.stack_info)
        return True


def configure_logging_redaction() -> None:
    """把敏感信息过滤器安装到应用与 Uvicorn 访问日志。"""
    filter_instance = SensitiveDataFilter()
    for logger_name in ("uvicorn.access", "homestay_bot"):
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(item, SensitiveDataFilter) for item in logger.filters
        ):
            logger.addFilter(filter_instance)

    # 记录从子 logger 传播时不会再次执行父 logger 的过滤器，必须把过滤器
    # 安装到当前已配置的 handler；这样 SQL、路径和访问日志都能统一脱敏。
    handlers: list[logging.Handler] = list(logging.getLogger().handlers)
    for item in logging.Logger.manager.loggerDict.values():
        if isinstance(item, logging.Logger):
            handlers.extend(item.handlers)
    seen: set[int] = set()
    for handler in handlers:
        if id(handler) in seen:
            continue
        seen.add(id(handler))
        if not any(
            isinstance(item, SensitiveDataFilter) for item in handler.filters
        ):
            handler.addFilter(filter_instance)


# 标记应用自己的 stderr handler，重复调用配置函数时据此避免重复输出。
_APPLICATION_HANDLER_NAME = "homestay_bot.stderr"


def configure_application_logging() -> None:
    """让应用日志以 INFO 级别输出到 stderr，并统一经过脱敏过滤器。

    Uvicorn 只给自己的 logger 配置 handler，根 logger 既无 handler 又是 WARNING，
    导致轮次规划、证据选择、主链耗时等 INFO 观测在生产全部丢失；子模块的 WARNING
    还会经 logging.lastResort 输出而绕过脱敏。这里只放开 homestay_bot 命名空间，
    第三方库的 INFO 保持关闭。propagate 保持默认 True：生产根 logger 没有 handler
    不会重复输出，同时测试的 caplog 仍能收到记录。

    ponytail: 级别固定为 INFO、不提供配置项；日志量增长到需要按环境调级或接入
    集中日志时，再引入级别配置或 dictConfig。
    """
    app_logger = logging.getLogger("homestay_bot")
    app_logger.setLevel(logging.INFO)
    if not any(item.name == _APPLICATION_HANDLER_NAME for item in app_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(_APPLICATION_HANDLER_NAME)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        app_logger.addHandler(handler)
    # 必须在挂好 handler 之后安装过滤器：子 logger 的记录只经过 handler 级过滤器。
    configure_logging_redaction()
