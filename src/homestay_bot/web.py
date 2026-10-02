"""统一管理后台模板环境与安全展示助手。"""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from homestay_bot.display import PAGE_MESSAGE_MAX_LENGTH, date_zh, status_zh
from homestay_bot.version import get_app_version, get_app_version_label

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")

# 客诉风险等级是分类器写入的字符串，只认这三个取值；与员工通知里的
# conversation_service._COMPLAINT_RISK_LABELS 同义。
_COMPLAINT_RISK_LABELS = {"critical": "严重", "high": "高", "normal": "一般"}


def complaint_risk_zh(value: object) -> str:
    """把客诉风险等级转成中文；未知取值显示「待核实」，不能被误读成低风险。"""
    return _COMPLAINT_RISK_LABELS.get(str(value or ""), "待核实")


def datetime_zh(value: object) -> str:
    """把时间统一转换为武汉本地时间，避免后台误读 UTC。"""
    if not isinstance(value, datetime):
        return "—"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    local = aware.astimezone(WUHAN_TIMEZONE)
    return f"{local.year}年{local.month}月{local.day}日 {local:%H:%M}"


def safe_external_url(value: object) -> str:
    """只允许 HTTPS 外链，拒绝脚本协议、凭据和畸形主机。"""
    if not isinstance(value, str) or len(value) > 2048:
        return "#"
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return "#"
    return value


PAGE_ERROR_SESSION_KEY = "page_error"
_PAGE_ERROR_MAX_LENGTH = PAGE_MESSAGE_MAX_LENGTH


def set_page_error(request: object, message: str) -> None:
    """记录一条读取即清除的失败提示，供重定向后的页面展示。

    只保存单条短消息且读取后立即删除，不会随浏览行为增长；长度上限避免签名
    会话 Cookie 因异常文本膨胀。
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    session[PAGE_ERROR_SESSION_KEY] = message[:_PAGE_ERROR_MAX_LENGTH]


def pop_page_error(request: object) -> str:
    """取出并清除失败提示；没有会话时返回空串。"""
    session = getattr(request, "session", None)
    if session is None:
        return ""
    value = session.pop(PAGE_ERROR_SESSION_KEY, "")
    return value if isinstance(value, str) else ""


PAGE_NOTICE_SESSION_KEY = "page_notice"


def set_page_notice(request: object, message: str) -> None:
    """记录一条读取即清除的成功提示，供重定向后的页面展示。

    与 set_page_error 同样是单条短消息、读取即删，不随浏览行为增长；签名会话
    Cookie 有大小上限，超出后会被浏览器整条丢弃而不是报错，表现为随机掉登录。
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    session[PAGE_NOTICE_SESSION_KEY] = message[:_PAGE_ERROR_MAX_LENGTH]


def pop_page_notice(request: object) -> str:
    """取出并清除成功提示；没有会话时返回空串。"""
    session = getattr(request, "session", None)
    if session is None:
        return ""
    value = session.pop(PAGE_NOTICE_SESSION_KEY, "")
    return value if isinstance(value, str) else ""


def base_template_context(request: object) -> dict[str, str]:
    """为全部后台模板提供统一产品名称、发布版本和一次性失败提示。"""
    return {
        "app_name": "YuMi 管理后台",
        "app_version": get_app_version(),
        "app_version_label": get_app_version_label(),
        "page_error": pop_page_error(request),
    }


templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent / "templates",
    context_processors=[base_template_context],
)
templates.env.filters.update(
    {
        "complaint_risk_zh": complaint_risk_zh,
        "date_zh": date_zh,
        "datetime_zh": datetime_zh,
        "enum_zh": status_zh,
        "status_zh": status_zh,
    }
)
templates.env.globals.update(
    {
        "safe_external_url": safe_external_url,
    }
)
