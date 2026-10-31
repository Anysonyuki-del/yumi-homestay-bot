import json
import logging
import re
import traceback
from datetime import datetime
from pathlib import PurePath
from typing import Protocol, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from homestay_bot.integrations.wechat_miniprogram import WeChatLoginError, WeChatLoginFailure
from homestay_bot.services.miniprogram_auth import (
    MiniProgramLogin,
    MiniProgramLoginThrottled,
    MiniProgramProfile,
)

router = APIRouter(prefix="/api/miniprogram")
logger = logging.getLogger(__name__)

LOGIN_MAX_BODY_BYTES = 1024
MAX_AUTHORIZATION_CHARS = 256
# wx.login 的 code 由可见 ASCII 组成；限定字符和长度后才允许发往微信。
_CODE_PATTERN = re.compile(r"[\x21-\x7e]{1,128}")
# secrets.token_urlsafe(32) 固定生成 43 个 URL 安全字符；格式不符不查数据库。
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")

# 微信失败分类到接口状态码与中文提示；只返回固定文案，不透传微信 errmsg。
_WECHAT_FAILURE_RESPONSES = {
    WeChatLoginFailure.CODE_INVALID: (
        status.HTTP_401_UNAUTHORIZED,
        "登录凭证已失效，请重新登录",
    ),
    WeChatLoginFailure.LOGIN_BLOCKED: (
        status.HTTP_403_FORBIDDEN,
        "当前微信账号暂时无法登录，请联系管家",
    ),
    WeChatLoginFailure.UPSTREAM_BUSY: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "微信登录服务繁忙，请稍后重试",
    ),
    WeChatLoginFailure.UPSTREAM_UNAVAILABLE: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "微信登录服务暂时不可用，请稍后重试",
    ),
}


class MiniProgramAuthPort(Protocol):
    """定义路由所需的小程序认证服务接口。"""

    async def login(self, code: str) -> MiniProgramLogin:
        """校验 code 并签发会话。"""

    async def profile(self, token: str) -> MiniProgramProfile | None:
        """核验 token 并返回最小资料。"""

    async def logout(self, token: str) -> None:
        """撤销当前会话。"""


def _error(status_code: int, error: str, message: str) -> JSONResponse:
    """返回统一的安全错误体，不回显任何请求内容。"""
    return JSONResponse({"error": error, "message": message}, status_code=status_code)


def _unauthorized() -> JSONResponse:
    """token 缺失、格式不符、无效或过期统一返回 401，不区分具体原因。"""
    return _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "登录已失效，请重新登录")


def _service(request: Request) -> MiniProgramAuthPort | None:
    """读取生命周期装配的认证服务；未配置小程序时为 None。"""
    return cast(
        MiniProgramAuthPort | None,
        getattr(request.app.state, "miniprogram_auth_service", None),
    )


def _not_configured() -> JSONResponse:
    """小程序配置缺失或无效时只停用本组接口。"""
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "not_configured",
        "小程序登录暂未开放",
    )


def _log_local_failure(stage: str, error: Exception) -> None:
    """记录本地故障的类型与出错位置，不记录异常正文。

    SQLAlchemy 等异常的正文带有 SQL 参数（含 AppID:OpenID 形式的 external_id），
    通用脱敏过滤器无法识别，因此这里不使用 exc_info；只保留异常类型、底层驱动异常
    类型和最内层栈帧的文件、行号与函数名，足够定位问题且不含任何请求数据。
    """
    origin = getattr(error, "orig", None)
    frames = traceback.extract_tb(error.__traceback__)
    location = (
        f"{PurePath(frames[-1].filename).name}:{frames[-1].lineno} {frames[-1].name}"
        if frames
        else "unknown"
    )
    logger.warning(
        "小程序认证本地故障：stage=%s error=%s origin=%s at=%s",
        stage,
        type(error).__name__,
        type(origin).__name__ if origin is not None else "-",
        location,
    )


def _iso(value: datetime) -> str:
    """以带时区的 ISO 8601 字符串返回到期时间。"""
    return value.isoformat()


async def _read_login_body(request: Request) -> bytes | None:
    """流式读取登录请求体，超过上限立即放弃并返回 None。"""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > LOGIN_MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > LOGIN_MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _bearer_token(request: Request) -> str | None:
    """从 Authorization 头取出格式合法的 Bearer token；任何不符都返回 None。"""
    header = request.headers.get("authorization")
    if header is None or len(header) > MAX_AUTHORIZATION_CHARS:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not _TOKEN_PATTERN.fullmatch(token):
        return None
    return token


@router.post("/login")
async def miniprogram_login(request: Request) -> Response:
    """用 wx.login 的一次性 code 登录；客户端不能提交身份、客户编号或权限。"""
    service = _service(request)
    if service is None:
        return _not_configured()

    body = await _read_login_body(request)
    code: object = None
    if body is not None:
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            code = payload.get("code")
    if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
        return _error(status.HTTP_400_BAD_REQUEST, "invalid_request", "登录请求无效，请重新登录")

    try:
        result = await service.login(code)
    except MiniProgramLoginThrottled:
        return _error(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "登录人数较多，请稍后重试")
    except WeChatLoginError as error:
        status_code, message = _WECHAT_FAILURE_RESPONSES[error.failure]
        return _error(status_code, error.failure.value, message)
    except Exception as error:
        # 数据库等本地故障：只记录类型与位置，客户端只拿到安全提示。
        _log_local_failure("login", error)
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable", "登录暂时不可用，请稍后重试"
        )
    return JSONResponse(
        {
            "token": result.token,
            "expires_at": _iso(result.expires_at),
            "display_name": result.display_name,
        }
    )


@router.get("/me")
async def miniprogram_me(request: Request) -> Response:
    """核验本机保存的 token，用于重新打开小程序时恢复登录状态。"""
    service = _service(request)
    if service is None:
        return _not_configured()
    token = _bearer_token(request)
    if token is None:
        return _unauthorized()
    try:
        profile = await service.profile(token)
    except Exception as error:
        _log_local_failure("me", error)
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "unavailable",
            "暂时无法确认登录状态，请稍后重试",
        )
    if profile is None:
        return _unauthorized()
    return JSONResponse(
        {"expires_at": _iso(profile.expires_at), "display_name": profile.display_name}
    )


@router.post("/logout")
async def miniprogram_logout(request: Request) -> Response:
    """撤销当前会话；会话已失效也返回 204，重复退出没有额外副作用。"""
    service = _service(request)
    if service is None:
        return _not_configured()
    token = _bearer_token(request)
    if token is None:
        return _unauthorized()
    try:
        await service.logout(token)
    except Exception as error:
        _log_local_failure("logout", error)
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "revoke_failed", "退出未完成，请重试")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
