"""微信小程序登录凭证校验（code2Session）客户端。

契约依据微信官方《小程序登录凭证校验》（页面最后编辑于 2026-07-04）：
GET /sns/jscode2session，查询参数 appid、secret、js_code、grant_type；
成功返回 openid、session_key（及可选 unionid），失败返回 errcode、errmsg。
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

WECHAT_API_BASE_URL = "https://api.weixin.qq.com"
CODE2SESSION_PATH = "/sns/jscode2session"
WECHAT_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 64 * 1024
MAX_OPENID_CHARS = 64

# 小程序 AppID 为 wx 加 16 位十六进制；格式不符按未配置处理，不发任何请求。
_APP_ID_PATTERN = re.compile(r"wx[0-9a-f]{16}")
# AppSecret 官方未公布格式，只拒绝空白和不可见字符，避免把明显填错的值发往微信。
_APP_SECRET_PATTERN = re.compile(r"[\x21-\x7e]{1,128}")
_OPENID_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{1,{MAX_OPENID_CHARS}}}")

logger = logging.getLogger(__name__)


class WeChatLoginFailure(StrEnum):
    """把微信错误归为客户端需要区别处理的几类结果。"""

    CODE_INVALID = "code_invalid"
    LOGIN_BLOCKED = "login_blocked"
    UPSTREAM_BUSY = "upstream_busy"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


# 只映射官方列出的接口专属错误码；其余错误码一律按上游不可用处理。
_ERRCODE_FAILURES = {
    40029: WeChatLoginFailure.CODE_INVALID,
    40226: WeChatLoginFailure.LOGIN_BLOCKED,
    45011: WeChatLoginFailure.UPSTREAM_BUSY,
    -1: WeChatLoginFailure.UPSTREAM_BUSY,
}


class WeChatLoginError(Exception):
    """表示一次 code 交换失败；只携带分类和错误码数字，不携带微信正文。"""

    def __init__(self, failure: WeChatLoginFailure, errcode: int | None = None) -> None:
        """保存失败分类与可选的微信错误码。"""
        super().__init__(failure.value)
        self.failure = failure
        self.errcode = errcode


@dataclass(frozen=True)
class MiniProgramCredentials:
    """保存服务端小程序凭据；密钥不进入 repr，避免被日志或异常带出。"""

    app_id: str
    app_secret: str = field(repr=False)


def load_miniprogram_credentials(
    app_id: str | None,
    app_secret: str | None,
) -> MiniProgramCredentials | None:
    """校验可选环境配置；缺项或格式无效时返回 None，由调用方只停用小程序登录。"""
    normalized_id = (app_id or "").strip()
    normalized_secret = (app_secret or "").strip()
    if not _APP_ID_PATTERN.fullmatch(normalized_id):
        return None
    if not _APP_SECRET_PATTERN.fullmatch(normalized_secret):
        return None
    return MiniProgramCredentials(app_id=normalized_id, app_secret=normalized_secret)


class WeChatMiniProgramClient:
    """向微信校验一次性登录 code，只返回 OpenID，不保存 session_key。"""

    def __init__(
        self,
        credentials: MiniProgramCredentials,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = WECHAT_TIMEOUT_SECONDS,
    ) -> None:
        """保存凭据与可替换传输层；连接按次创建，无需在应用关闭时回收。"""
        self._credentials = credentials
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    @property
    def app_id(self) -> str:
        """返回用作身份命名空间的 AppID。"""
        return self._credentials.app_id

    async def exchange_code(self, code: str) -> str:
        """用 code 换取 OpenID；code 只能用一次，因此任何失败都不自动重试。"""
        try:
            # httpx 的超时按阶段计算，外层再限制总时长，避免慢速响应拖住在途名额。
            async with asyncio.timeout(self._timeout_seconds):
                body = await self._fetch(code)
        except (TimeoutError, httpx.HTTPError) as error:
            # 异常文本可能含带 secret、js_code 的完整 URL，只记录类型并切断异常链。
            logger.warning("微信登录凭证校验请求失败：%s", type(error).__name__)
            raise WeChatLoginError(WeChatLoginFailure.UPSTREAM_UNAVAILABLE) from None
        return self._parse(body)

    async def _fetch(self, code: str) -> bytes:
        """发出单次请求并按上限读取正文；非 2xx 与重定向都视为上游异常。"""
        async with httpx.AsyncClient(
            base_url=WECHAT_API_BASE_URL,
            timeout=self._timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
        ) as client, client.stream(
            "GET",
            CODE2SESSION_PATH,
            params={
                "appid": self._credentials.app_id,
                "secret": self._credentials.app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
        ) as response:
            if not response.is_success:
                logger.warning("微信登录凭证校验返回 HTTP %s", response.status_code)
                raise WeChatLoginError(WeChatLoginFailure.UPSTREAM_UNAVAILABLE)
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > MAX_RESPONSE_BYTES:
                    logger.warning("微信登录凭证校验响应超过 %s 字节", MAX_RESPONSE_BYTES)
                    raise WeChatLoginError(WeChatLoginFailure.UPSTREAM_UNAVAILABLE)
                chunks.append(chunk)
            return b"".join(chunks)

    @staticmethod
    def _parse(body: bytes) -> str:
        """按 JSON 解析正文（不依赖 Content-Type），校验 errcode 与 openid。"""
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            logger.warning("微信登录凭证校验响应不是 JSON 对象")
            raise WeChatLoginError(WeChatLoginFailure.UPSTREAM_UNAVAILABLE)

        # 官方示例成功时也带 errcode: 0，因此缺失与 0 都算成功；布尔值不是错误码。
        errcode = payload.get("errcode", 0)
        if isinstance(errcode, bool) or not isinstance(errcode, int):
            logger.warning("微信登录凭证校验响应的 errcode 类型无效")
            raise WeChatLoginError(WeChatLoginFailure.UPSTREAM_UNAVAILABLE)
        if errcode != 0:
            failure = _ERRCODE_FAILURES.get(errcode, WeChatLoginFailure.UPSTREAM_UNAVAILABLE)
            # 只记录错误码数字，errmsg 由微信生成，不进入日志或客户端响应。
            logger.warning("微信登录凭证校验失败：errcode=%s", errcode)
            raise WeChatLoginError(failure, errcode)

        openid = payload.get("openid")
        if not isinstance(openid, str) or not _OPENID_PATTERN.fullmatch(openid):
            logger.warning("微信登录凭证校验响应缺少有效 openid")
            raise WeChatLoginError(WeChatLoginFailure.UPSTREAM_UNAVAILABLE)
        return openid
