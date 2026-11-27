"""真实同源页面与临时 SQLite 验证统一视觉主题、无障碍对比度、多断点布局与 Hallmark 回归。"""

import socket
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.orm import Session
from starlette.staticfiles import StaticFiles
from test_admin_batch_workbench import admin_client  # noqa: F401
from test_admin_interactions import browser, playwright_runtime  # noqa: F401

from homestay_bot.domain.models import PropertyProfile, StayOrder
from homestay_bot.web import templates

STATIC_ROOT = Path(__file__).resolve().parents[2] / "src/homestay_bot/static"
PREVIEW_ROOT = Path("/tmp/yumi-ae-theme-preview")
# 两套配色方案下的画布色（app.css 的 --page），首帧与无脚本页面都必须直接落在这里。
PAGE_BACKGROUND = {"light": "rgb(246, 244, 238)", "dark": "rgb(21, 20, 19)"}
THEME_COLOR = {"light": "#f6f4ee", "dark": "#151413"}
# 页面内计算 WCAG 对比度的脚本片段：rgb/rgba 字符串 → 相对亮度 → 对比度。
_CONTRAST_JS = """
  const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
  const luminance = value => rgb(value).map(x => x / 255)
    .map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
    .reduce((sum, x, i) => sum + x * [.2126, .7152, .0722][i], 0);
  const contrast = (fg, bg) => {
    const values = [luminance(fg), luminance(bg)].sort((a, b) => a - b);
    return (values[1] + .05) / (values[0] + .05);
  };
"""


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


def _context(instance, site, *, width=1280, script="", java_script_enabled=True,
             has_touch=False, color_scheme="light", reduced_motion="no-preference"):
    """隔离浏览器上下文，只允许本地读请求，记录并阻断任何写请求和外网访问。

    color_scheme 模拟系统明暗偏好（页面纯 CSS 跟随，无切换器）；reduced_motion 模拟减少动态。
    """
    origin, cookies = site
    context = instance.new_context(
        viewport={"width": width, "height": 840}, java_script_enabled=java_script_enabled,
        has_touch=has_touch, color_scheme=color_scheme, reduced_motion=reduced_motion,
    )
    context.add_cookies(cookies)
    if script:
        context.add_init_script(script)
    writes = []

    def guard(route):
        """验收纯视觉与无障碍表现；经营写入和所有出网都在浏览器边界阻断。"""
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


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("path", ["/employee/login", "/employee/admin"])
def test_unified_theme_renders_on_first_content_frame(theme_site, browser, path, scheme):  # noqa: F811
    """冷加载与跨认证/后台页面在首个内容帧就落在系统明暗对应的画布色，无闪屏、无切换器。"""
    script = """
      function observeContent() {
        const heading = document.querySelector('h1');
        if (heading && heading.getBoundingClientRect().height > 0) {
          window.firstContentFrame = {
            background: getComputedStyle(document.body).backgroundColor,
          };
        } else requestAnimationFrame(observeContent);
      }
      requestAnimationFrame(observeContent);
    """
    context, page, writes = _context(browser, theme_site, script=script, color_scheme=scheme)
    try:
        origin = theme_site[0]
        other = "/employee/admin" if path.endswith("login") else "/employee/login"
        for target in (path, other):
            page.goto(origin + target)
            page.wait_for_function("window.firstContentFrame !== undefined")
            frame = page.evaluate("window.firstContentFrame")
            assert frame == {"background": PAGE_BACKGROUND[scheme]}
            # 浏览器按 media 挑选生效的那条 theme-color，两条都必须存在且与画布同色。
            for mode, color in THEME_COLOR.items():
                assert page.locator(
                    f'meta[name="theme-color"][media="(prefers-color-scheme: {mode})"]'
                ).get_attribute("content") == color
            assert page.locator("[data-theme-control]").count() == 0
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_javascript_keeps_unified_theme_and_core_pages_usable(theme_site, browser, scheme):  # noqa: F811
    """禁用 JavaScript 时，认证页与任务页在两种系统明暗下都保持统一外观且核心控件可操作。"""
    context, page, writes = _context(
        browser, theme_site, java_script_enabled=False, color_scheme=scheme,
    )
    try:
        for path in ("/employee/login", "/employee/tasks"):
            page.goto(theme_site[0] + path)
            assert page.locator("body").evaluate(
                "node => getComputedStyle(node).backgroundColor"
            ) == PAGE_BACKGROUND[scheme]
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


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_theme_pages_fit_scrollbar_width_without_overflow(
    theme_site, playwright_runtime, scheme,  # noqa: F811
):
    """两套外壳、核心业务页在浅暗两态、真实滚动条槽位下从 320px 到 1440px 均不产生横向溢出。

    同时按明暗各导出 390px / 1440px 代表页截图，供人工复核光学视觉。
    """
    instance = playwright_runtime.chromium.launch(
        headless=True, ignore_default_args=["--hide-scrollbars"],
    )
    context, page, writes = _context(instance, theme_site, color_scheme=scheme)
    try:
        paths = (
            "/employee/login",
            "/employee/admin",
            "/employee/tasks",
            "/employee/admin/operations",
            "/employee/customers/1?tab=service",
        )
        for path in paths:
            page.goto(theme_site[0] + path)
            page.add_style_tag(content="html { scrollbar-gutter: stable; overflow-y: scroll; }")
            for width in (320, 360, 390, 1280, 1440):
                page.set_viewport_size({"width": width, "height": 840})
                size = page.evaluate("""() => ({
                  client: document.documentElement.clientWidth,
                  scroll: document.documentElement.scrollWidth,
                  body: document.body.getBoundingClientRect().width,
                })""")
                assert size["client"] < width
                assert size["scroll"] <= size["client"], (path, width, size)
                assert size["body"] <= size["client"], (path, width, size)
                preview_paths = (
                    "/employee/admin",
                    "/employee/admin/operations",
                    "/employee/login",
                )
                if path in preview_paths and width in (390, 1440):
                    PREVIEW_ROOT.mkdir(exist_ok=True)
                    slug = path.strip("/").replace("/", "-")
                    page.screenshot(
                        path=str(PREVIEW_ROOT / f"{slug}-{scheme}-{width}.png"), full_page=True,
                        animations="disabled",
                    )
        assert writes == []
    finally:
        context.close()
        instance.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_and_action_contrast_uses_actual_computed_surfaces(theme_site, browser, scheme):  # noqa: F811
    """浅暗两态下实际浏览器背景、徽标、按钮、禁用态与焦点环全部达到 WCAG AA 阈值。"""
    context, page, writes = _context(browser, theme_site, color_scheme=scheme)
    try:
        page.goto(theme_site[0] + "/employee/admin")
        badge = templates.env.get_template("components/ui.html").module.badge
        samples = "".join(str(badge("合成状态", tone)) for tone in (
            "success", "warning", "danger", "info", "neutral",
        ))
        page.locator(".dashboard-grid .panel").first.evaluate(
            "(node, html) => node.insertAdjacentHTML('beforeend', html)", samples,
        )
        page.locator(".topbar__account").focus()
        selectors = (
            ".eyebrow, .muted, .first-list__why, .first-list__room span,"
            " .badge, a.button, .topbar__account"
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
            if (node.matches(':focus-visible')) {
              samples.push({ className: 'focus', min: 3,
                             ratio: contrast(style.outlineColor, bg) });
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
        # 导出色盲视觉模拟截图供人工复核。
        page.goto(theme_site[0] + "/employee/tasks")
        page.locator('input[name="task_ids"]:visible').first.check()
        session = context.new_cdp_session(page)
        PREVIEW_ROOT.mkdir(exist_ok=True)
        for vision in ("achromatopsia", "deuteranopia"):
            session.send("Emulation.setEmulatedVisionDeficiency", {"type": vision})
            page.evaluate(
                "document.activeElement.blur(); window.scrollTo({top: 0, behavior: 'instant'});",
            )
            page.screenshot(
                path=str(PREVIEW_ROOT / f"ae-{scheme}-{vision}.png"),
                animations="disabled", full_page=True,
            )
        session.send("Emulation.setEmulatedVisionDeficiency", {"type": "none"})
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_visible_control_boundaries_and_focus_have_sufficient_contrast(theme_site, browser, scheme):  # noqa: F811
    """浅暗两态下空控件在普通、焦点和无效状态均能辨认，禁用状态保持不可操作且褪去主色。"""
    context, page, writes = _context(browser, theme_site, color_scheme=scheme)
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
                    selector, state, ratios
                )
                if state == "focus":
                    assert ratios["focus"] is not None and ratios["focus"] >= 3
            control.evaluate("node => { node.disabled = true; }")
            assert control.is_disabled()
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_optical_surfaces_stay_readable_in_both_schemes(theme_site, browser, scheme):  # noqa: F811
    """暗色曾隐式依赖白色 --surface：轻提示、跳转链接与图片预览改用专属令牌；占比条顶光不压低分段。

    分项阈值：

    - 轻提示前景与底色、跳转链接前景与底色 >= 4.5:1；
    - 图片预览底在两态都是固定浅中性色，深色线条的透明 PNG 才看得见；
    - 占比条每个分段叠上 ::after 顶光最亮处后，相对槽底仍 >= 3:1。
    """
    context, page, writes = _context(browser, theme_site, color_scheme=scheme)
    try:
        page.goto(theme_site[0] + "/employee/admin")
        # 只核对规格列出的语义色分段（成功/中性已入住/告警/危险）；
        # 逐一注入，不依赖种子数据的房态分布。
        page.locator(".readiness-bar").evaluate("""node => {
          node.replaceChildren(...['ready', 'occupied', 'cleaning', 'maintenance'].map(status => {
            const span = document.createElement('span');
            span.className = `readiness-bar__seg readiness-bar__seg--${status}`;
            span.style.flexGrow = '1';
            return span;
          }));
        }""")
        page.locator(".dashboard-grid .panel").first.evaluate(
            "node => node.insertAdjacentHTML('beforeend',"
            " '<ul class=\"image-list\"><li><img alt=\"合成预览\"></li></ul>')"
        )
        page.locator("[data-toast-region]").evaluate(
            "node => { node.textContent = '合成提示'; node.hidden = false; }"
        )
        result = page.evaluate("() => {" + _CONTRAST_JS + """
          const toast = getComputedStyle(document.querySelector('[data-toast-region]'));
          const skip = getComputedStyle(document.querySelector('.skip-link'));
          const bar = document.querySelector('.readiness-bar');
          const well = getComputedStyle(bar).backgroundColor;
          const sheen = getComputedStyle(bar, '::after').backgroundImage;
          const alpha = Number(sheen.match(/rgba\\(255, 255, 255, ([\\d.]+)\\)/)[1]);
          const lit = value => 'rgb(' + rgb(value)
            .map(x => Math.round(x * (1 - alpha) + 255 * alpha)).join(', ') + ')';
          const segments = [...bar.querySelectorAll('.readiness-bar__seg')].map(node => {
            const color = getComputedStyle(node).backgroundColor;
            return { className: node.className, ratio: contrast(lit(color), well) };
          });
          return {
            toast: contrast(toast.color, toast.backgroundColor),
            skip: contrast(skip.color, skip.backgroundColor),
            media: getComputedStyle(document.querySelector('.image-list img')).backgroundColor,
            alpha, segments,
          };
        }""")
        assert result["toast"] >= 4.5, result
        assert result["skip"] >= 4.5, result
        assert result["media"] == "rgb(236, 232, 225)", result
        assert result["alpha"] <= 0.16, result
        for segment in result["segments"]:
            assert segment["ratio"] >= 3, (scheme, segment)
        assert len(result["segments"]) == 4
        assert writes == []
    finally:
        context.close()


@pytest.mark.parametrize("reduced", [False, True])
def test_dark_dialog_enters_with_spring_unless_motion_is_reduced(theme_site, browser, reduced):  # noqa: F811
    """暗色下确认弹窗按 panel 弹簧入场；减少动态时弹窗与 ::backdrop 都不再有任何动画。"""
    context, page, writes = _context(
        browser, theme_site, color_scheme="dark",
        reduced_motion="reduce" if reduced else "no-preference",
    )
    try:
        page.goto(theme_site[0] + "/employee/admin")
        state = page.evaluate("""() => {
          const dialog = document.querySelector('.confirm-dialog');
          dialog.showModal();
          const own = getComputedStyle(dialog);
          const backdrop = getComputedStyle(dialog, '::backdrop');
          const snapshot = { animation: own.animationName,
                             backdropTransition: backdrop.transitionDuration };
          dialog.close();
          return snapshot;
        }""")
        if reduced:
            assert state == {"animation": "none", "backdropTransition": "0s"}, state
        else:
            assert state["animation"] == "dialog-enter", state
        assert writes == []
    finally:
        context.close()


# —— Hallmark 审查回归（docs/specs/2026-10-04_hallmark-frontend-polish-spec.md R2）——

_LINE_COUNT = """node => {
  const range = document.createRange();
  range.selectNodeContents(node);
  const tops = [...range.getClientRects()].filter(rect => rect.width > 0)
    .map(rect => Math.round(rect.top));
  return new Set(tops).size;
}"""

_MIN_TARGET = 44 - 0.5
_FREEZE_ELAPSED = "Object.defineProperty(performance, 'now', { value: () => 0 });"


def _seed_rooms(tmp_path, rooms):
    """向本测试独立的 SQLite 追加合成房间；今日退房让房间进入工作台「先处理」。"""
    engine = create_sync_engine(f"sqlite:///{tmp_path / 'admin.db'}")
    today = date(2026, 10, 2)
    try:
        with Session(engine) as session:
            for property_id, title, room_number in rooms:
                session.add(PropertyProfile(
                    id=property_id, title=title, room_number=room_number, is_active=True,
                ))
                session.add(StayOrder(
                    hostex_reservation_code=f"synthetic-{property_id}",
                    stay_code=f"synthetic-{property_id}", property_id=property_id,
                    customer_id=1, status="confirmed",
                    check_in_date=today - timedelta(days=1), check_out_date=today,
                ))
            session.commit()
    finally:
        engine.dispose()


def test_hallmark_heading_actions_keep_full_labels(theme_site, browser):  # noqa: F811
    """H01：窄屏入口位于标题下方并左对齐，标签始终单行且不覆盖文字。"""
    context, page, writes = _context(browser, theme_site)
    try:
        cases = [
            ("/employee/admin", ".workbench-first > .section-heading",
             "/employee/admin/operations"),
            ("/employee/admin/operations", ".operations-heading > .section-heading",
             "/employee/admin/attention"),
        ]
        for path, heading, href in cases:
            page.goto(theme_site[0] + path)
            for width in (320, 375, 414, 480, 520, 521, 768, 1280):
                page.set_viewport_size({"width": width, "height": 840})
                button = page.locator(f"{heading} > .button")
                assert button.get_attribute("href") == href
                assert button.evaluate(_LINE_COUNT) == 1, (path, width)
                box = button.bounding_box()
                text = page.locator(f"{heading} > :first-child").bounding_box()
                overlaps = (
                    box["x"] < text["x"] + text["width"] and text["x"] < box["x"] + box["width"]
                    and box["y"] < text["y"] + text["height"]
                    and text["y"] < box["y"] + box["height"]
                )
                assert not overlaps, (path, width, box, text)
                if width <= 520:
                    assert box["y"] >= text["y"] + text["height"] - .5, (path, width, box, text)
                    assert abs(box["x"] - text["x"]) <= .5, (path, width)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                ), (path, width)
        assert writes == []
    finally:
        context.close()


def test_dashboard_metric_content_does_not_overlap(theme_site, browser):  # noqa: F811
    """真实指标的标签、数值、说明和入口不重叠，各分栏边界均可完整阅读。"""
    context, page, writes = _context(browser, theme_site)
    try:
        page.goto(theme_site[0] + "/employee/admin")
        metric = page.locator(".metric-item").first
        assert metric.locator("small").inner_text() == "客人、客诉和待确认事项"
        assert metric.locator("a").get_attribute("href") == "/employee/admin/attention"
        for width in (320, 375, 414, 480, 520, 521, 768, 1280):
            page.set_viewport_size({"width": width, "height": 840})
            results = page.locator(".metric-item").evaluate_all("""nodes => nodes.map(node => {
              const outer = node.getBoundingClientRect();
              const boxes = [...node.children].map(child => child.getBoundingClientRect());
              const inside = boxes.every(r => r.left >= outer.left - .5
                && r.right <= outer.right + .5 && r.top >= outer.top - .5
                && r.bottom <= outer.bottom + .5);
              const overlaps = boxes.some((a, i) => boxes.slice(i + 1).some(b =>
                Math.min(a.right, b.right) - Math.max(a.left, b.left) > .5
                && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > .5));
              return {inside, overlaps};
            })""")
            assert results and all(
                result == {"inside": True, "overlaps": False} for result in results
            ), (width, results)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            ), width
        assert writes == []
    finally:
        context.close()


def test_hallmark_event_duration_fits_available_width(theme_site, browser, tmp_path):  # noqa: F811
    """H02：最长时长在真实事件块内保持单行，不留孤字，也不压住姓名和计划时间。"""
    _seed_rooms(tmp_path, [(301, "合成长名房间", "301")])
    context, page, writes = _context(browser, theme_site, script=_FREEZE_ELAPSED)
    try:
        page.goto(theme_site[0] + "/employee/admin/operations")
        assert page.locator(".room-event .countdown strong").count() >= 2
        samples = ("不足 1 分钟", "59 分钟", "1 小时 27 分钟", "23 小时 59 分钟", "2 天 23 小时")
        for width in (320, 375, 414, 768, 1280):
            page.set_viewport_size({"width": width, "height": 840})
            for sample in samples:
                results = page.locator(".room-event").evaluate_all("""(events, sample) =>
                  events.filter(event => event.querySelector('.countdown strong')).map(event => {
                    const strong = event.querySelector('.countdown strong');
                    strong.textContent = sample;
                    const range = document.createRange();
                    range.selectNodeContents(strong);
                    const lines = new Set([...range.getClientRects()]
                      .filter(rect => rect.width > 0).map(rect => Math.round(rect.top))).size;
                    const box = rect => rect.getBoundingClientRect();
                    const own = box(strong), area = box(event);
                    const hit = other => {
                      const r = box(other);
                      return own.left < r.right && r.left < own.right
                        && own.top < r.bottom && r.top < own.bottom;
                    };
                    return {
                      lines,
                      inside: own.left >= area.left - .5 && own.right <= area.right + .5,
                      overlap: [...event.querySelectorAll('.room-event__guest, .room-event__abs')]
                        .some(hit),
                    };
                  })""", sample)
                assert results, width
                for result in results:
                    assert result == {"lines": 1, "inside": True, "overlap": False}, (
                        width, sample, result,
                    )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            ), width
        assert writes == []
    finally:
        context.close()


def test_hallmark_touch_navigation_targets_remain_reachable(theme_site, browser):  # noqa: F811
    """H03：触屏下导航、页签与账号入口至少 44×44；纯鼠标桌面保持紧凑密度。"""
    for width in (390, 768):
        context, page, writes = _context(browser, theme_site, width=width, has_touch=True)
        try:
            page.goto(theme_site[0] + "/employee/admin")
            assert page.evaluate("matchMedia('(any-pointer: coarse)').matches")
            account = page.locator(".topbar__account").bounding_box()
            assert account["height"] >= _MIN_TARGET and account["width"] >= _MIN_TARGET, (
                width, account)
            tabs = page.locator(".tab-nav a:visible").evaluate_all(
                "nodes => nodes.map(node => node.getBoundingClientRect().height)")
            assert tabs and min(tabs) >= _MIN_TARGET, (width, tabs)
            opener = page.locator("[data-drawer-open]")
            opener.click()
            page.wait_for_function(
                "document.querySelector('[data-drawer]').getBoundingClientRect().left >= 0")
            heights = page.locator(".side-nav a, .sidebar-footer .account-link").evaluate_all(
                "nodes => nodes.map(node => node.getBoundingClientRect().height)")
            assert heights and min(heights) >= _MIN_TARGET, (width, heights)
            footer = page.locator(".sidebar-footer .account-link")
            footer.scroll_into_view_if_needed()
            assert footer.evaluate("""node => {
              const r = node.getBoundingClientRect();
              const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return r.bottom <= innerHeight && node.contains(hit);
            }""")
            page.keyboard.press("Escape")
            page.wait_for_function(
                "document.activeElement === document.querySelector('[data-drawer-open]')")
            assert writes == []
        finally:
            context.close()
    context, page, writes = _context(browser, theme_site, width=1280)
    try:
        page.goto(theme_site[0] + "/employee/admin")
        assert not page.evaluate("matchMedia('(any-pointer: coarse)').matches")
        tab = page.locator(".tab-nav a").first.bounding_box()
        assert tab["height"] < 44, tab
        assert writes == []
    finally:
        context.close()


def test_hallmark_first_room_identity_is_not_duplicated(theme_site, browser, tmp_path):  # noqa: F811
    """H04：缺房号或房号与房名相同只显示房名一次；有不同房号时保持房号加房名。"""
    _seed_rooms(tmp_path, [
        (401, "无房号房", None), (402, "空房号房", ""), (403, "空白房号房", "   "),
        (404, "同名房", "同名房"), (405, "带房号房", "A12"),
    ])
    context, page, writes = _context(browser, theme_site)
    try:
        page.goto(theme_site[0] + "/employee/admin")
        rows = page.locator(".first-list__room").evaluate_all("""nodes => nodes.map(node => ({
          href: node.getAttribute('href'),
          parts: [...node.children].map(child => child.textContent.trim()),
          text: node.textContent,
        }))""")
        by_href = {row["href"]: row for row in rows}
        for property_id, title in [(401, "无房号房"), (402, "空房号房"),
                                   (403, "空白房号房"), (404, "同名房")]:
            row = by_href[f"/employee/properties/{property_id}"]
            assert row["parts"] == [title], row
            assert row["text"].count(title) == 1 and "房号待补充" not in row["text"], row
        assert by_href["/employee/properties/405"]["parts"] == ["A12", "带房号房"]
        assert by_href["/employee/properties/101"]["parts"] == ["101", "合成房间"]
        assert page.locator(".first-list li .first-list__why").count() == len(rows)
        assert writes == []
    finally:
        context.close()
