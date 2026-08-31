"""为后台敏感页面统一补齐禁止缓存的响应头。"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 后台全部页面都可能渲染客人档案、客诉正文、订单或凭证入口，一律禁止留存。
NO_STORE_PATH_PREFIXES = ("/employee",)


def _requires_no_store(path: str) -> bool:
    """判断请求路径是否属于必须禁止缓存的后台面。"""
    return any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in NO_STORE_PATH_PREFIXES
    )


class AdminNoStoreMiddleware:
    """以纯 ASGI 方式补充 no-store，避免包装流式文件响应。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游应用。"""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """只为后台路径补齐缓存、防嵌套和来源保护头，其余请求原样透传。"""
        if scope["type"] != "http" or not _requires_no_store(scope.get("path", "")):
            await self._app(scope, receive, send)
            return

        async def send_with_no_store(message: Message) -> None:
            """在响应头发出前统一收紧后台缓存、嵌套展示和来源泄漏策略。"""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["cache-control"] = "no-store"
                headers["content-security-policy"] = "frame-ancestors 'none'"
                headers["x-frame-options"] = "DENY"
                headers["referrer-policy"] = "no-referrer"
                if "x-content-type-options" not in headers:
                    headers["x-content-type-options"] = "nosniff"
            await send(message)

        await self._app(scope, receive, send_with_no_store)
