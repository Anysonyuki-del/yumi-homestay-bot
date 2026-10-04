"""真实同源页面与临时 SQLite 验证主题行为，合成数据不连接生产或外部服务。"""

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from starlette.staticfiles import StaticFiles
from test_admin_batch_workbench import admin_client  # noqa: F401
from test_admin_interactions import browser, playwright_runtime  # noqa: F401

from homestay_bot.web import templates

STATIC_ROOT = Path(__file__).resolve().parents[2] / "src/homestay_bot/static"
PREVIEW_ROOT = Path("/tmp/yumi-warm-theme-preview")


@pytest.fixture
def theme_site(admin_client):  # noqa: F811
    """复用真实会话门面和 SQLite 装配，以实际 HTTP 加载模板及本站静态资源。"""
    app = admin_client.app
    app.mount("/static", StaticFiles(directory=STATIC_ROOT))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(
            app, log_level="error", lifespan="off", ws="none",
        ))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "临时同源服务未能在期限内启动"
            cookies = [
                {"name": key, "value": value, "url": origin}
                for key, value in admin_client.cookies.items()
            ]
            yield origin, cookies
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            assert not worker.is_alive(), "临时服务必须释放线程和监听端口"


def _context(instance, site, *, width=1280, script="", java_script_enabled=True):
    """隔离浏览器偏好，只允许本地读请求，记录并阻断任何写请求。"""
    origin, cookies = site
    context = instance.new_context(
        viewport={"width": width, "height": 840}, java_script_enabled=java_script_enabled,
    )
    context.add_cookies(cookies)
    if script:
        context.add_init_script(script)
    writes = []

    def guard(route):
        """验收纯视觉切换；经营写入和所有出网都在浏览器边界阻断。"""
        if route.request.method not in {"GET", "HEAD"}:
            writes.append(route.request.method)
            route.abort()
        elif not route.request.url.startswith(origin + "/"):
            writes.append("external " + route.request.method)
            route.abort()
        else:
            route.continue_()

    context.route("**/*", guard)
    return context, context.new_page(), writes


def _control(page, *, width=1280):
    """从可见入口切换；手机沿用真实抽屉，不强行操作隐藏控件。"""
    if page.locator("#theme-auth").count():
        return page.locator("#theme-auth")
    if width < 1024:
        page.locator("[data-drawer-open]").click()
        return page.locator("#theme-drawer")
    return page.locator("#theme-topbar")


@pytest.mark.parametrize("saved, expected", [(None, "classic"), ("warm", "warm"),
                                             ("invalid", "classic")])
@pytest.mark.parametrize("path", ["/employee/login", "/employee/admin"])
def test_theme_restores_on_first_content_frame(theme_site, browser, saved, expected, path):  # noqa: F811
    """冷加载、刷新、跨认证/后台页面均在首个内容帧恢复，不靠手工注入资源。"""
    preference = "" if saved is None else (
        f"localStorage.setItem('yumi.admin.theme', {json.dumps(saved)});"
    )
    script = preference + """
      // 在含真实标题的首帧记录计算底色，而不是只检查中间 data-theme 属性。
      function observeContent() {
        const heading = document.querySelector('h1');
        if (heading && heading.getBoundingClientRect().height > 0) {
          window.firstContentFrame = {
            theme: document.documentElement.dataset.theme,
            background: getComputedStyle(document.body).backgroundColor,
          };
        } else requestAnimationFrame(observeContent);
      }
      requestAnimationFrame(observeContent);
    """
    context, page, writes = _context(browser, theme_site, script=script)
    try:
        origin = theme_site[0]
        other = "/employee/admin" if path.endswith("login") else "/employee/login"
        for target in (path, path, other):
            page.goto(origin + target)
            page.wait_for_function("window.firstContentFrame !== undefined")
            frame = page.evaluate("window.firstContentFrame")
            assert frame == {
                "theme": expected,
                "background": "rgb(250, 249, 245)" if expected == "warm"
                else "rgb(244, 246, 250)",
            }
            assert _control(page).input_value() == expected
            assert page.locator('meta[name="theme-color"]').get_attribute("content") == (
                "#faf9f5" if expected == "warm" else "#f4f6fa"
            )
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_switch_preserves_selection_draft_and_focus(theme_site, browser, width):  # noqa: F811
    """真实选择及未保存房态不丢，主题同步无提交，抽屉键盘惯例保留。"""
    context, page, writes = _context(browser, theme_site, width=width)
    try:
        origin = theme_site[0]
        page.goto(origin + "/employee/tasks")
        selected = page.locator('input[name="task_ids"]:visible').first
        selected.check()
        selected_id = selected.input_value()
        toolbar = page.locator(".selection-actions")
        before = toolbar.bounding_box()
        control = _control(page, width=width)
        # select_option 派发选择事件而不模拟键盘焦点；先聚焦才能验证切换不夺走焦点。
        control.focus()
        control.select_option("warm")
        assert selected.is_checked()
        assert selected.input_value() == selected_id
        assert page.locator("[data-theme-control]").evaluate_all(
            "nodes => nodes.every(node => node.value === 'warm')"
        )
        assert control.evaluate("node => document.activeElement === node")
        if width < 1024:
            assert page.locator("[data-drawer]").get_attribute("aria-hidden") is None
            page.keyboard.press("Escape")
            # 关闭动画完成后才恢复焦点，不改现有抽屉的过渡/取消协议。
            page.wait_for_function("""() => document.activeElement ===
              document.querySelector('[data-drawer-open]')""")
            assert page.locator("[data-drawer-open]").evaluate(
                "node => document.activeElement === node"
            )
            after = toolbar.bounding_box()
            assert abs(before["height"] - after["height"]) <= 1
        page.reload()
        assert page.locator("html").get_attribute("data-theme") == "warm"
        auth = context.new_page()
        auth.goto(origin + "/employee/login")
        assert auth.locator("#theme-auth").input_value() == "warm"
        page.goto(origin + "/employee/admin/operations")
        page.locator(".room-adjust > summary").first.click()
        status = page.locator(".room-status-form select").first
        status.select_option("maintenance")
        csrf = page.locator('.room-status-form input[name="csrf_token"]').first.input_value()
        control = _control(page, width=width)
        control.select_option("classic")
        assert status.input_value() == "maintenance"
        assert page.locator(
            '.room-status-form input[name="csrf_token"]',
        ).first.input_value() == csrf
        assert page.url == origin + "/employee/admin/operations"
        # 已打开的标签页保持自身外观，下一次加载才读到本浏览器的新选择。
        assert auth.locator("html").get_attribute("data-theme") == "warm"
        auth.reload()
        assert auth.locator("#theme-auth").input_value() == "classic"
        # 合成 submit 事件走正式忙态监听器，不执行浏览器表单默认动作或业务写入。
        page.once("dialog", lambda dialog: dialog.accept())
        form = page.locator(".room-status-form").first
        form.evaluate("""node => node.dispatchEvent(new SubmitEvent('submit', {
          bubbles: true, cancelable: true, submitter: node.querySelector('button'),
        }))""")
        submitter = form.locator("button")
        page.wait_for_function("document.querySelector('.room-status-form button').disabled")
        control.select_option("warm")
        assert submitter.is_disabled()
        assert submitter.get_attribute("aria-busy") == "true"
        assert submitter.inner_text() == "正在处理…"
        assert form.get_attribute("data-submitting") == "true"
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("failure", ["read", "write"])
def test_storage_failure_is_honest_and_keeps_page_usable(theme_site, browser, failure):  # noqa: F811
    """读取失败用经典，写入失败仍即时切换并提示未保存，刷新不假称持久化。"""
    if failure == "read":
        script = """Object.defineProperty(window, 'localStorage', {
          get() { throw new DOMException('blocked', 'SecurityError'); }
        });"""
        initial, target = "classic", "warm"
    else:
        script = """localStorage.setItem('yumi.admin.theme', 'warm');
          Storage.prototype.setItem = function() {
            throw new DOMException('full', 'QuotaExceededError');
          };"""
        initial, target = "warm", "classic"
    context, page, writes = _context(browser, theme_site, script=script)
    try:
        page.goto(theme_site[0] + "/employee/login")
        assert page.locator("html").get_attribute("data-theme") == initial
        page.fill("#username", "合成草稿")
        _control(page).select_option(target)
        assert page.locator("html").get_attribute("data-theme") == target
        assert page.locator("#username").input_value() == "合成草稿"
        feedback = page.locator("#theme-auth-feedback")
        assert feedback.is_visible()
        assert feedback.inner_text() == "本次已切换，浏览器无法记住此选择"
        page.reload()
        assert page.locator("html").get_attribute("data-theme") == initial
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("disabled", [True, False])
def test_missing_script_keeps_classic_and_hides_switch(theme_site, browser, disabled):  # noqa: F811
    """无 JavaScript 或仅主题资源失败时经典仍可操作，不出现无效选择入口。"""
    context, page, writes = _context(
        browser, theme_site, java_script_enabled=not disabled,
        script="localStorage.setItem('yumi.admin.theme', 'warm');",
    )
    if not disabled:
        context.route("**/static/theme.js?*", lambda route: route.abort())
    try:
        for path in ("/employee/login", "/employee/tasks"):
            page.goto(theme_site[0] + path)
            assert page.locator("[data-theme-control]:visible").count() == 0
            assert page.locator("body").evaluate(
                "node => getComputedStyle(node).backgroundColor"
            ) == "rgb(244, 246, 250)"
            assert page.locator("h1").is_visible()
            if path.endswith("login"):
                page.locator("#username").fill("合成草稿")
                assert page.locator('button[type="submit"]').is_enabled()
            else:
                selected = page.locator('input[name="task_ids"]:visible').first
                selected.check()
                assert selected.is_checked()
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["classic", "warm"])
def test_theme_pages_fit_scrollbar_width_and_show_one_control(
    theme_site, playwright_runtime, theme,  # noqa: F811
):
    """真实两套外壳、业务页与滚动条槽位均不溢出，主题控件不挤占手机顶部。"""
    instance = playwright_runtime.chromium.launch(
        headless=True, ignore_default_args=["--hide-scrollbars"],
    )
    script = f"localStorage.setItem('yumi.admin.theme', {json.dumps(theme)});"
    context, page, writes = _context(instance, theme_site, script=script)
    try:
        paths = ("/employee/login", "/employee/admin", "/employee/tasks",
                 "/employee/admin/operations", "/employee/customers/1?tab=service")
        for path in paths:
            page.goto(theme_site[0] + path)
            page.add_style_tag(content="html { scrollbar-gutter: stable; overflow-y: scroll; }")
            for width in (320, 360, 390, 1280):
                page.set_viewport_size({"width": width, "height": 840})
                size = page.evaluate("""() => ({
                  client: document.documentElement.clientWidth,
                  scroll: document.documentElement.scrollWidth,
                  body: document.body.getBoundingClientRect().width,
                })""")
                assert size["client"] < width
                assert size["scroll"] <= size["client"], (theme, path, width, size)
                assert size["body"] <= size["client"], (theme, path, width, size)
                count = 1 if path.endswith("login") or width >= 1024 else 0
                assert page.locator("[data-theme-control]:visible").count() == count
                if theme == "warm" and path == "/employee/admin" and width in (390, 1280):
                    PREVIEW_ROOT.mkdir(exist_ok=True)
                    page.screenshot(
                        path=str(PREVIEW_ROOT / f"warm-{width}.png"), full_page=True,
                        animations="disabled",
                    )
        assert writes == []
    finally:
        context.close()
        instance.close()


def test_warm_text_and_action_contrast_uses_actual_computed_surfaces(theme_site, browser):  # noqa: F811
    """实际浏览器背景、徽标及交互色达到阈值，捕获浅卡片语义文字不足的原缺陷。"""
    context, page, writes = _context(
        browser, theme_site, script="localStorage.setItem('yumi.admin.theme', 'warm');",
    )
    try:
        page.goto(theme_site[0] + "/employee/admin")
        badge = templates.env.get_template("components/ui.html").module.badge
        samples = "".join(str(badge("合成状态", tone)) for tone in (
            "success", "warning", "danger", "info", "neutral",
        ))
        page.locator(".dashboard-grid .panel").first.evaluate(
            "(node, html) => node.insertAdjacentHTML('beforeend', html)", samples,
        )
        # 原生控件的文字、边界与键盘焦点分别按实际计算样式检查。
        page.locator("#theme-topbar").focus()
        selectors = (
            ".eyebrow, .muted, .first-list__why, .first-list__room span, .badge, a.button, select"
        )
        results = page.locator(selectors).evaluate_all("""nodes => {
          const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
          const luminance = value => rgb(value).map(x => x / 255)
            .map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
            .reduce((sum, x, i) => sum + x * [.2126, .7152, .0722][i], 0);
          const contrast = (fg, bg) => {
            const values = [luminance(fg), luminance(bg)].sort((a,b) => a-b);
            return (values[1] + .05) / (values[0] + .05);
          };
          return nodes.filter(node => node.getClientRects().length).flatMap(node => {
            let ancestor = node;
            while (getComputedStyle(ancestor).backgroundColor === 'rgba(0, 0, 0, 0)')
              ancestor = ancestor.parentElement;
            const style = getComputedStyle(node);
            const bg = getComputedStyle(ancestor).backgroundColor;
            const samples = [{ className: node.className, foreground: style.color,
                              background: bg, ratio: contrast(style.color, bg), min: 4.5 }];
            if (node.tagName === 'SELECT') {
              samples.push({ className: 'control border', min: 3,
                             ratio: contrast(style.borderColor, bg) });
              if (node.matches(':focus-visible')) {
                samples.push({ className: 'focus', min: 3,
                               ratio: contrast(style.outlineColor, bg) });
              }
            }
            return samples;
          });
        }""")
        assert len(results) >= 7
        assert any(result["className"] == "focus" for result in results)
        for result in results:
            assert result["ratio"] >= result["min"], result
        metric = page.locator(".metric-item").first
        description = metric.locator("small").bounding_box()
        link = metric.locator("a").bounding_box()
        assert description["y"] + description["height"] <= link["y"]
        # 复用真实日期宏覆盖当前入住与重点订单的小字，不手写另一套日历结构。
        page.goto(theme_site[0] + "/employee/admin/operations")
        text = page.locator(".cal-m__state--now")
        if text.count():
            assert text.first.evaluate("node => getComputedStyle(node).color") == "rgb(61, 61, 58)"
        page.goto(theme_site[0] + "/employee/admin")
        action = page.locator(".first-list .button").first
        if action.count():
            action.hover()
            # 悬停有 160ms 颜色过渡；等待终态而非在指针移动当帧读取起点。
            page.wait_for_function("""() => getComputedStyle(
              document.querySelector('.first-list .button')
            ).color === 'rgb(250, 249, 245)'""")
            assert action.evaluate("node => getComputedStyle(node).color") == "rgb(250, 249, 245)"
        # 仅导出真实合成任务页的视觉模拟，人工复核填充/描边和状态文案的辨认性。
        page.goto(theme_site[0] + "/employee/tasks")
        page.locator('input[name="task_ids"]:visible').first.check()
        session = context.new_cdp_session(page)
        PREVIEW_ROOT.mkdir(exist_ok=True)
        for vision in ("achromatopsia", "deuteranopia"):
            session.send("Emulation.setEmulatedVisionDeficiency", {"type": vision})
            # 连续全页截图前解除控件焦点并回到顶部，避免恢复视口时焦点滚动移动固定栏。
            page.evaluate(
                "document.activeElement.blur(); window.scrollTo({top: 0, behavior: 'instant'});",
            )
            page.screenshot(
                path=str(PREVIEW_ROOT / f"warm-{vision}.png"),
                animations="disabled", full_page=True,
            )
        session.send("Emulation.setEmulatedVisionDeficiency", {"type": "none"})
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["classic", "warm"])
def test_visible_control_boundaries_and_focus_have_sufficient_contrast(theme_site, browser, theme):  # noqa: F811
    """真实页面的空控件在普通、焦点和无效状态均能辨认，禁用状态保持不可操作。"""
    context, page, writes = _context(browser, theme_site,
        script=f"localStorage.setItem('yumi.admin.theme', '{theme}');")
    try:
        for path, selector in [
            ("/employee/tasks", '.create-task input[type=date]'),
            ("/employee/tasks", '#bulk-assign-employee'),
            ("/employee/customers/1?tab=memory", '.memory-edit textarea[name=short_summary]'),
        ]:
            page.goto(theme_site[0] + path)
            if "memory" in path:
                page.locator('.memory-edit > summary').click()
            elif "input" in selector:
                page.locator('.create-task > summary').click()
            control = page.locator(selector).first
            assert control.is_visible()
            for state in ("ordinary", "focus", "invalid"):
                if state == "focus":
                    control.focus()
                elif state == "invalid":
                    control.evaluate("node => node.setAttribute('aria-invalid', 'true')")
                ratios = control.evaluate("""node => {
                  const lum = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number)
                    .map(x => x / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055)/1.055)**2.4)
                    .reduce((sum, x, i) => sum + x * [.2126, .7152, .0722][i], 0);
                  const contrast = (a, b) =>
                    (Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
                  let parent = node.parentElement;
                  while (getComputedStyle(parent).backgroundColor === 'rgba(0, 0, 0, 0)')
                    parent = parent.parentElement;
                  const style = getComputedStyle(node);
                  const outside = getComputedStyle(parent).backgroundColor;
                  return { inside: contrast(style.borderTopColor, style.backgroundColor),
                    outside: contrast(style.borderTopColor, outside),
                    focus: node.matches(':focus-visible')
                      ? contrast(style.outlineColor, outside) : null };
                }""")
                assert ratios["inside"] >= 3 and ratios["outside"] >= 3, (
                    theme, selector, state, ratios
                )
                if state == "focus":
                    assert ratios["focus"] is not None and ratios["focus"] >= 3
            control.evaluate("node => { node.disabled = true; }")
            assert control.is_disabled()
        assert writes == []
    finally:
        context.close()
