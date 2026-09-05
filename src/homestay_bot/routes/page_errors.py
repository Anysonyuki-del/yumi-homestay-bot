"""把页面服务的领域异常统一转换为稳定的 HTTP 行为。"""

import logging
import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from homestay_bot.domain.errors import OperationRefused
from homestay_bot.web import set_page_error

logger = logging.getLogger(__name__)


def raise_page_error(
    error: Exception,
    *,
    forbidden: str,
    not_found: str,
    unknown: str,
    log_subject: str,
) -> None:
    """按异常类型抛出稳定状态；只有刻意写给用户的拒绝才回显原文。

    页面默认不回显异常原文：SQL 片段、文件路径和凭据都可能出现在异常文本里。
    OperationRefused 及其子类是唯一例外，它们原样上抛交由全局处理器展示；其余
    异常只记录类型与追踪号，页面仅得到调用方给定的通用文案。

    三段文案由调用方传入而不是从主题拼装：各页面的既有措辞是用户可见字符串，
    统一机制不应顺手改写它们。
    """
    if isinstance(error, OperationRefused):
        raise error
    if isinstance(error, PermissionError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=forbidden,
        ) from error
    if isinstance(error, LookupError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=not_found,
        ) from error
    trace_id = secrets.token_hex(8)
    logger.error(
        "%s：error_type=%s trace_id=%s",
        log_subject,
        type(error).__name__,
        trace_id,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=unknown,
    ) from error


def _wants_html(request: Request) -> bool:
    """判断请求期望页面而不是接口响应。"""
    accept = request.headers.get("accept", "")
    return "text/html" in accept


_FALLBACK_RETURN_PATH = "/employee/tasks"


def safe_return_path(candidate: str | None) -> str:
    """校验回跳路径；只接受本站 /employee/ 下的相对路径，避免开放重定向。

    不使用 Referer：本应用发送 `referrer-policy: no-referrer`，该请求头在真实
    浏览器里恒为空，靠它推断来源会让每次失败都落到兜底地址、丢掉当前筛选。
    """
    if not candidate:
        return _FALLBACK_RETURN_PATH
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return _FALLBACK_RETURN_PATH
    if not parsed.path.startswith("/employee/"):
        return _FALLBACK_RETURN_PATH
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


async def handle_operation_refused(
    request: Request,
    exc: Exception,
) -> Response:
    """把业务拒绝还原成带提示的原页面，而不是一坨 JSON。

    表单提交失败时用重定向回原页面（PRG），刷新不会重复提交，也不必在处理器里
    重建整页数据。接口调用继续得到 JSON。
    """
    message = str(exc)
    status_code = getattr(exc, "status_code", status.HTTP_409_CONFLICT)
    if not _wants_html(request):
        return JSONResponse({"detail": message}, status_code=status_code)
    set_page_error(request, message)
    return RedirectResponse(
        safe_return_path(getattr(exc, "return_to", None)),
        status_code=status.HTTP_303_SEE_OTHER,
    )
