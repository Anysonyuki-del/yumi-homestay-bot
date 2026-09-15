"""阶段 A 的 WebView 技术验证 fixture。

这个服务器只用来证明客户端外壳的平台能力：Cookie 与重定向是否按预期传递、
POST 是否被客户端重放、inline 私有图片能否保存、上传取消与拒绝路径是否可辨识、
自动外跳与 target=_blank 是否被导航策略拦住。

它不是业务后台：没有真实账号库、没有真实 CSRF 服务、没有权限模型，
因此它的通过结果不能用于 A04 与 A07 的业务验收（见 Spec 第 9.1 节）。
真实认证、CSRF 原子消费与会话撤销必须连接 AdminAuthService 与真实路由验证。

只依赖标准库。测试凭据由运行时参数提供，不写入本文件、日志或安装包。
"""

from __future__ import annotations

import argparse
import base64
import html
import http.cookies
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 1×1 透明 PNG，用作私有图片场景的最小有效响应体。
_TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    b"YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

SESSION_COOKIE = "yumi_fixture_session"

# 外部域名只用于验证导航策略会拦住自动外跳，fixture 自身不会请求它。
EXTERNAL_URL = "https://example.com/external-target"


class FixtureState:
    """跨请求共享的计数与令牌状态，全部在内存中，进程退出即消失。"""

    def __init__(self, username: str, password: str) -> None:
        self._lock = threading.Lock()
        self._username = username
        self._password = password
        # 有效会话 id 集合。会话撤销在这里只是删除条目，
        # 不等价于业务后台的会话版本与闲置超时裁决。
        self._sessions: set[str] = set()
        # 一次性 CSRF 令牌，消费后立即删除，用于观察客户端是否重放 POST。
        self._csrf_tokens: set[str] = set()
        # 按路径统计的 POST 次数，供 A07 判断客户端有没有额外发第二次请求。
        self._post_counts: dict[str, int] = {}

    def check_credentials(self, username: str, password: str) -> bool:
        return secrets.compare_digest(username, self._username) and secrets.compare_digest(
            password, self._password
        )

    def issue_session(self) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._sessions.add(token)
        return token

    def session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return token in self._sessions

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.discard(token)

    def issue_csrf(self) -> str:
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._csrf_tokens.add(token)
        return token

    def consume_csrf(self, token: str | None) -> bool:
        """一次性消费。第二次提交同一令牌必然失败，用于暴露 POST 重放。"""
        if not token:
            return False
        with self._lock:
            if token in self._csrf_tokens:
                self._csrf_tokens.discard(token)
                return True
        return False

    def count_post(self, path: str) -> int:
        with self._lock:
            self._post_counts[path] = self._post_counts.get(path, 0) + 1
            return self._post_counts[path]

    def snapshot_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._post_counts)

    def reset_counts(self) -> None:
        with self._lock:
            self._post_counts.clear()


def _page(title: str, body: str) -> bytes:
    """最小页面模板。带 viewport，便于同时观察 360/390 宽度下的表现。"""
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px/1.6 system-ui,sans-serif;margin:16px;max-width:720px}"
        "a,button{min-height:44px} pre{overflow-x:auto;background:#f4f4f4;padding:8px}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>"
    ).encode()


class FixtureHandler(BaseHTTPRequestHandler):
    """技术验证路由。每个分支对应 Spec 第 9.1 节列出的一个可观察场景。"""

    server_version = "YuMiWebViewFixture/0.1"
    state: FixtureState  # 由 build_server 注入

    # ---- 基础工具 ----

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(raw)
        except http.cookies.CookieError:
            return None
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _authenticated(self) -> bool:
        return self.state.session_valid(self._session_token())

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        extra_headers: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers or ():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, location: str, status: int = 302) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _login_html(self, status: int = 200) -> None:
        """登录页。

        A09 的关键场景：私有图片在会话失效时返回的正是这个 200 HTML，
        原生下载实现必须识别出它不是图片，不能保存成文件并报告成功。
        """
        token = self.state.issue_csrf()
        body = (
            '<form method="post" action="/login">'
            f'<input type="hidden" name="csrf" value="{html.escape(token)}">'
            '<p><label>账号 <input name="username" autocomplete="username"></label></p>'
            '<p><label>密码 <input name="password" type="password" '
            'autocomplete="current-password"></label></p>'
            '<p><button type="submit">登录</button></p>'
            "</form>"
        )
        self._send(status, _page("登录", body))

    # ---- GET ----

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        route = parsed.path

        if route in ("/", "/workstation"):
            if not self._authenticated():
                # 受保护页面在缺失有效身份时跳转登录，与业务后台的行为一致。
                self._redirect("/login")
                return
            self._workstation()
            return

        if route == "/login":
            # 登录 GET 直接渲染页面，不反映现有会话是否有效。
            self._login_html()
            return

        if route == "/private/image.png":
            self._private_image()
            return

        if route == "/private/expired.png":
            # 无论是否登录都返回 200 登录 HTML，固定复现失效 Cookie 场景。
            self._login_html()
            return

        if route == "/private/forbidden.png":
            self._send(403, _page("无权限", "<p>该附件不属于当前任务。</p>"))
            return

        if route == "/private/redirect.png":
            # 逐跳同源限制的反例：原生下载不得跟随到外部地址，更不得带 Cookie。
            self._redirect(EXTERNAL_URL)
            return

        if route == "/private/truncated.png":
            self._truncated_download()
            return

        if route == "/autoredirect":
            # 页面加载后立即外跳，用于验证无用户手势时不会自动打开系统浏览器。
            self._redirect(EXTERNAL_URL)
            return

        if route == "/blank-link":
            body = (
                f'<p><a href="{html.escape(EXTERNAL_URL)}" target="_blank" rel="noopener">'
                "外部链接（新窗口）</a></p>"
                '<p><a href="/workstation" target="_blank">同源链接（新窗口）</a></p>'
            )
            self._send(200, _page("新窗口场景", body))
            return

        if route == "/slow":
            # 超过启动等待阈值，用于观察“连接较慢”提示与重试行为。
            delay = float(parse_qs(parsed.query).get("seconds", ["20"])[0])
            time.sleep(min(delay, 60.0))
            self._send(200, _page("慢响应", "<p>响应完成。</p>"))
            return

        if route == "/error500":
            # 业务错误正文必须原样可读，不能被客户端替换成“断网”。
            self._send(500, _page("服务器错误", "<p>订单处理失败：库存记录冲突。</p>"))
            return

        if route == "/counts":
            rows = "".join(
                f"<tr><td>{html.escape(path)}</td><td>{count}</td></tr>"
                for path, count in sorted(self.state.snapshot_counts().items())
            )
            self._send(200, _page("POST 计数", f"<table>{rows}</table>"))
            return

        self._send(404, _page("未找到", "<p>没有这个路径。</p>"))

    def _workstation(self) -> None:
        body = (
            "<p>已登录。以下链接覆盖阶段 A 的可观察场景。</p>"
            "<h2>写操作计数</h2>"
            '<form method="post" action="/counted">'
            f'<input type="hidden" name="csrf" value="{html.escape(self.state.issue_csrf())}">'
            '<button type="submit">提交一次（一次性 CSRF）</button></form>'
            '<p><a href="/counts">查看 POST 计数</a></p>'
            "<h2>上传</h2>"
            '<form method="post" action="/upload" enctype="multipart/form-data">'
            '<input type="file" name="photo"><button type="submit">上传</button></form>'
            '<form method="post" action="/upload/reject" enctype="multipart/form-data">'
            '<input type="file" name="photo">'
            '<button type="submit">上传（服务端拒绝）</button></form>'
            "<h2>私有图片</h2>"
            '<p>正常：<img src="/private/image.png" alt="私有图片" width="120" height="120"></p>'
            "<p>会话失效返回 200 HTML："
            '<img src="/private/expired.png" alt="失效场景" width="120" height="120"></p>'
            '<p>无权限 403：<img src="/private/forbidden.png" alt="403 场景"></p>'
            '<p>外域重定向：<img src="/private/redirect.png" alt="重定向场景"></p>'
            '<p>中断传输：<img src="/private/truncated.png" alt="中断场景"></p>'
            "<h2>导航</h2>"
            '<p><a href="/blank-link">新窗口场景</a></p>'
            '<p><a href="/autoredirect">自动外跳</a></p>'
            '<p><a href="/slow">慢响应</a> · <a href="/error500">业务错误</a></p>'
            "<h2>退出</h2>"
            '<form method="post" action="/logout">'
            f'<input type="hidden" name="csrf" value="{html.escape(self.state.issue_csrf())}">'
            '<button type="submit">退出登录</button></form>'
        )
        self._send(200, _page("工作台（fixture）", body))

    def _private_image(self) -> None:
        """需要会话的 inline 图片。

        响应头刻意与业务后台一致：`routes/private_files.py` 的
        `download_private_file` 与 `routes/properties.py` 的
        `download_property_qr` 都以 `filename=None` 构造 FileResponse，
        因此私有附件不带 Content-Disposition，只有 media_type、
        Cache-Control: no-store 和 X-Content-Type-Options: nosniff。

        这对客户端是硬约束：原生“保存图片”拿不到服务器给定的文件名，
        必须自行从 URL 末段与响应 Content-Type 推导，并按 Spec F-04
        第 5 条去除路径成分和危险字符。
        """
        if not self._authenticated():
            self._login_html()
            return
        self._send(
            200,
            _TINY_PNG,
            content_type="image/png",
            extra_headers=(
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ),
        )

    def _truncated_download(self) -> None:
        """声明的长度大于实际写入，连接提前关闭。

        用于确认下载失败不会被报告成成功，且不会留下半个文件。
        """
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(_TINY_PNG) + 4096))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(_TINY_PNG[: len(_TINY_PNG) // 2])
        self.wfile.flush()
        self.close_connection = True

    # ---- POST ----

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        route = urlparse(self.path).path

        if route == "/login":
            self._login_submit()
            return

        if not self._authenticated():
            self._login_html()
            return

        if route == "/logout":
            self.state.revoke_session(self._session_token())
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/counted":
            fields = self._read_form()
            count = self.state.count_post(route)
            token = fields.get("csrf", [""])[0]
            if not self.state.consume_csrf(token):
                # 令牌已被消费说明这是重放；计数仍然递增，便于区分
                # “客户端多发了一次”与“服务端没收到”。
                self._send(
                    403,
                    _page("CSRF 失败", f"<p>令牌无效或已使用。累计收到 {count} 次。</p>"),
                )
                return
            # PRG：成功后重定向，避免刷新重放。
            self._redirect("/counts", status=303)
            return

        if route in ("/upload", "/upload/reject"):
            self._upload(reject=route.endswith("/reject"))
            return

        self._send(404, _page("未找到", "<p>没有这个路径。</p>"))

    def _login_submit(self) -> None:
        fields = self._read_form()
        if not self.state.consume_csrf(fields.get("csrf", [""])[0]):
            self._send(403, _page("CSRF 失败", "<p>令牌无效或已使用，请重新打开登录页。</p>"))
            return
        username = fields.get("username", [""])[0]
        password = fields.get("password", [""])[0]
        if not self.state.check_credentials(username, password):
            self._send(401, _page("登录失败", "<p>账号或密码不正确。</p>"))
            return
        token = self.state.issue_session()
        self.send_response(303)
        self.send_header("Location", "/workstation")
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _upload(self, reject: bool) -> None:
        count = self.state.count_post("/upload")
        filename, size = self._read_upload()
        if filename is None:
            # 用户取消系统选择器时，多数 WebView 会提交一个空文件域。
            self._send(400, _page("未选择文件", f"<p>没有收到文件。累计 {count} 次。</p>"))
            return
        if reject:
            self._send(
                415,
                _page(
                    "格式不受支持",
                    f"<p>不支持的文件类型：{html.escape(filename)}。</p>",
                ),
            )
            return
        self._send(
            200,
            _page(
                "上传成功",
                f"<p>收到 {html.escape(filename)}，{size} 字节。累计 {count} 次。</p>",
            ),
        )

    # ---- 请求体解析 ----

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_form(self) -> dict[str, list[str]]:
        return parse_qs(self._read_body().decode("utf-8", "replace"), keep_blank_values=True)

    def _read_upload(self) -> tuple[str | None, int]:
        """解析 multipart 的 photo 字段，返回文件名与字节数。

        手写解析而不用标准库 cgi：该模块已在 Python 3.13 移除，
        fixture 需要在项目当前和后续解释器版本上都能直接运行。
        文件名保留原样，中文名不做转码，才能验证上传路径的中文表现。
        """
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            return None, 0
        match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not match:
            return None, 0
        boundary = (match.group(1) or match.group(2)).strip().encode("utf-8")
        body = self._read_body()

        for part in body.split(b"--" + boundary):
            head_sep = part.find(b"\r\n\r\n")
            if head_sep == -1:
                continue
            raw_headers = part[:head_sep].decode("utf-8", "replace")
            if 'name="photo"' not in raw_headers:
                continue
            name_match = re.search(r'filename="([^"]*)"', raw_headers)
            if not name_match or not name_match.group(1):
                # 取消系统选择器时，多数 WebView 仍提交一个 filename 为空的字段。
                return None, 0
            # 各部分之间以 \r\n 分隔，尾部的分隔符不属于文件内容。
            content = part[head_sep + 4 :]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            return name_match.group(1), len(content)
        return None, 0

    def log_message(self, fmt: str, *args: object) -> None:
        # 只记录方法与路径，不记录 Cookie、表单正文或查询参数中的凭据。
        parsed = urlparse(self.path)
        # flush=True：日志重定向到文件时 stdout 会缓冲，
        # 观察期间进程若被结束就看不到已发生的请求。
        print(f"[fixture] {self.command} {parsed.path} -> {fmt % args}", flush=True)


def build_server(host: str, port: int, username: str, password: str) -> ThreadingHTTPServer:
    handler = type(
        "BoundFixtureHandler", (FixtureHandler,), {"state": FixtureState(username, password)}
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="YuMi 客户端阶段 A 的 WebView 技术验证 fixture")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址。默认只监听本机；真机测试时显式指定受控局域网地址，不做公网转发。",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--username",
        default=os.environ.get("YUMI_FIXTURE_USER", "tester"),
        help="测试账号，仅运行时提供，不写入文档或安装包。",
    )
    parser.add_argument("--password", default=os.environ.get("YUMI_FIXTURE_PASSWORD"))
    args = parser.parse_args()

    if not args.password:
        parser.error("必须通过 --password 或 YUMI_FIXTURE_PASSWORD 提供测试密码")

    server = build_server(args.host, args.port, args.username, args.password)
    print(f"fixture 监听 http://{args.host}:{args.port}/ （仅技术验证，非业务后台）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
