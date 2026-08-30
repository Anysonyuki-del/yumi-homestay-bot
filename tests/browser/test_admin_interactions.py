from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADMIN_SCRIPT = (PROJECT_ROOT / "src/homestay_bot/static/admin.js").read_text()
ADMIN_CSS = (PROJECT_ROOT / "src/homestay_bot/static/app.css").read_text()


def _admin_fixture() -> str:
    """返回包含真实后台选择器的最小页面。"""
    return """<!doctype html>
    <html lang="zh-CN"><head></head><body class="admin-body">
      <div class="admin-shell">
        <aside class="admin-sidebar" id="admin-drawer" data-drawer inert aria-hidden="true">
          <button type="button" data-drawer-close>关闭</button>
          <a href="#drawer-target">任务中心</a>
        </aside>
        <div class="drawer-backdrop" data-drawer-close hidden></div>
        <div class="admin-workspace">
          <header class="topbar">
            <button class="drawer-trigger" type="button" data-drawer-open
                    aria-controls="admin-drawer" aria-expanded="false">打开导航</button>
            <div class="topbar-title">后台页面标题</div><a href="#account">账号</a>
          </header>
          <main class="page-content">
            <div class="alert alert--success" role="status">保存成功</div>
            <details class="operations-overview"><summary>查看紧凑总览</summary>
              <div>总览内容</div>
            </details>
            <a href="#workspace-target">工作区链接</a>
            <form id="form-a" data-unsaved-warning>
              <input name="a"><button type="submit">保存 A</button>
            </form>
            <form id="form-b" data-unsaved-warning>
              <input name="b"><button type="submit">保存 B</button>
            </form>
          </main>
        </div>
      </div>
      <details class="no-script-nav"><summary>打开页面导航</summary>
        <a href="#fallback-target">任务中心</a>
      </details>
      <div id="drawer-target"></div><div id="workspace-target"></div>
      <div id="fallback-target"></div>
    </body></html>"""


def _load_admin_page(page: Page) -> None:
    """载入真实 CSS 和 JavaScript，模拟后台渐进增强页面。"""
    page.set_content(_admin_fixture())
    page.add_style_tag(content=ADMIN_CSS)
    page.add_script_tag(content=ADMIN_SCRIPT)


@pytest.fixture(scope="module")
def playwright_runtime() -> Iterator[Playwright]:
    """按模块启动并关闭 Playwright 运行时。"""
    with sync_playwright() as runtime:
        yield runtime


@pytest.fixture(scope="module")
def browser(playwright_runtime: Playwright) -> Iterator[Browser]:
    """使用 Playwright 标准安装位置启动无头 Chromium。"""
    instance = playwright_runtime.chromium.launch(headless=True)
    yield instance
    instance.close()


def test_cross_form_submission(browser: Browser) -> None:
    """取消跨表单提交不锁按钮，确认后清理状态并阻止重复提交。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _load_admin_page(page)
    page.fill("#form-a input", "尚未保存")
    page.evaluate(
        """() => {
          window.confirmCalls = [];
          window.confirm = (message) => {
            window.confirmCalls.push(message);
            return false;
          };
        }"""
    )

    current_form = page.evaluate(
        """async () => {
          const form = document.querySelector("#form-a");
          const accepted = form.dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true })
          );
          await new Promise((resolve) => setTimeout(resolve, 10));
          const unload = new Event("beforeunload", { cancelable: true });
          window.dispatchEvent(unload);
          return {
            accepted,
            submitting: form.dataset.submitting,
            disabled: form.querySelector("button").disabled,
            motionClass: form.querySelector("button").classList.contains("is-submitting"),
            unloadPrevented: unload.defaultPrevented,
            confirmCalls: window.confirmCalls.length,
          };
        }"""
    )
    assert current_form == {
        "accepted": True,
        "submitting": "true",
        "disabled": True,
        "motionClass": True,
        "unloadPrevented": False,
        "confirmCalls": 0,
    }

    page.goto("about:blank")
    _load_admin_page(page)
    page.fill("#form-a input", "尚未保存")
    page.evaluate(
        """() => {
          window.confirmCalls = [];
          window.confirm = (message) => {
            window.confirmCalls.push(message);
            return false;
          };
        }"""
    )
    cancelled = page.evaluate(
        """async () => {
          const form = document.querySelector("#form-b");
          const accepted = form.dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true })
          );
          await new Promise((resolve) => setTimeout(resolve, 10));
          return {
            accepted,
            submitting: form.dataset.submitting || "",
            disabled: form.querySelector("button").disabled,
            confirmCalls: window.confirmCalls,
          };
        }"""
    )
    assert cancelled["accepted"] is False
    assert cancelled["submitting"] == ""
    assert cancelled["disabled"] is False
    assert len(cancelled["confirmCalls"]) == 1

    page.evaluate(
        """() => {
          window.confirm = (message) => {
            window.confirmCalls.push(message);
            return true;
          };
        }"""
    )
    confirmed = page.evaluate(
        """async () => {
          const form = document.querySelector("#form-b");
          const first = form.dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true })
          );
          const second = form.dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true })
          );
          await new Promise((resolve) => setTimeout(resolve, 10));
          const unload = new Event("beforeunload", { cancelable: true });
          window.dispatchEvent(unload);
          return {
            first,
            second,
            submitting: form.dataset.submitting,
            disabled: form.querySelector("button").disabled,
            unloadPrevented: unload.defaultPrevented,
          };
        }"""
    )
    assert confirmed == {
        "first": True,
        "second": False,
        "submitting": "true",
        "disabled": True,
        "unloadPrevented": False,
    }
    page.close()


def test_drawer_accessibility(browser: Browser) -> None:
    """关闭抽屉不可 Tab，打开可访问，ESC 恢复焦点，桌面恢复可用。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _load_admin_page(page)
    drawer = page.locator("[data-drawer]")
    assert drawer.evaluate(
        """node => ({
          inert: node.inert,
          ariaHidden: node.getAttribute("aria-hidden"),
          visibility: getComputedStyle(node).visibility,
        })"""
    ) == {"inert": True, "ariaHidden": "true", "visibility": "hidden"}

    page.focus("[data-drawer-open]")
    page.keyboard.press("Tab")
    assert page.evaluate(
        '() => document.activeElement.closest("[data-drawer]") !== null'
    ) is False
    page.click("[data-drawer-open]")
    assert drawer.evaluate("node => node.inert") is False
    assert drawer.get_attribute("aria-hidden") is None
    assert drawer.locator("[data-drawer-close]").evaluate(
        "node => node === document.activeElement"
    ) is True
    page.keyboard.press("Escape")
    assert page.locator("[data-drawer-open]").evaluate(
        "node => node === document.activeElement"
    ) is False
    assert drawer.evaluate("node => node.inert") is False
    page.wait_for_timeout(260)
    assert page.locator("[data-drawer-open]").evaluate(
        "node => node === document.activeElement"
    ) is True
    assert drawer.evaluate("node => node.inert") is True
    assert drawer.evaluate("node => getComputedStyle(node).visibility") == "hidden"

    page.click("[data-drawer-open]")
    page.evaluate("() => { closeDrawer(); openDrawer(); }")
    page.wait_for_timeout(260)
    assert drawer.evaluate("node => node.classList.contains('is-open')") is True
    assert drawer.evaluate("node => node.inert") is False
    assert page.locator(".drawer-backdrop").evaluate(
        "node => !node.hidden && node.classList.contains('is-visible')"
    ) is True

    page.set_viewport_size({"width": 1100, "height": 844})
    page.wait_for_timeout(20)
    assert drawer.evaluate("node => node.inert") is False
    assert drawer.get_attribute("aria-hidden") is None
    page.focus('[data-drawer] a[href="#drawer-target"]')
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(20)
    assert drawer.evaluate("node => node.inert") is True
    page.close()


def test_motion_feedback_and_reduced_motion(browser: Browser) -> None:
    """常规模式提供克制反馈，减少动态模式保留静态终态。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _load_admin_page(page)
    assert page.locator(".page-content").evaluate(
        "node => getComputedStyle(node).animationName"
    ) == "page-enter"
    assert page.locator(".alert").evaluate(
        "node => getComputedStyle(node).animationName"
    ) == "status-enter"

    summary = page.locator(".operations-overview > summary")
    closed_transform = summary.evaluate(
        "node => getComputedStyle(node, '::after').transform"
    )
    summary.click()
    page.wait_for_timeout(220)
    open_transform = summary.evaluate(
        "node => getComputedStyle(node, '::after').transform"
    )
    assert closed_transform != "none"
    assert open_transform != closed_transform

    page.evaluate(
        """() => document.querySelector('#form-a').dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true })
        )"""
    )
    page.wait_for_timeout(10)
    submitter = page.locator("#form-a button")
    assert submitter.evaluate("node => node.classList.contains('is-submitting')") is True
    assert submitter.evaluate(
        "node => getComputedStyle(node, '::before').animationName"
    ) == "submit-spin"
    page.close()

    context = browser.new_context(
        reduced_motion="reduce",
        viewport={"width": 390, "height": 844},
    )
    reduced_page = context.new_page()
    _load_admin_page(reduced_page)
    assert reduced_page.locator(".page-content").evaluate(
        "node => getComputedStyle(node).animationName"
    ) == "none"
    assert reduced_page.locator(".alert").evaluate(
        "node => getComputedStyle(node).animationName"
    ) == "none"
    reduced_page.evaluate(
        """() => document.querySelector('#form-a').dispatchEvent(
          new Event('submit', { bubbles: true, cancelable: true })
        )"""
    )
    reduced_page.wait_for_timeout(10)
    assert reduced_page.locator("#form-a button").evaluate(
        "node => getComputedStyle(node, '::before').animationName"
    ) == "none"
    context.close()


@pytest.mark.parametrize("width", [375, 1440])
def test_motion_layout_has_no_horizontal_overflow(
    browser: Browser,
    width: int,
) -> None:
    """验证手机和桌面关键宽度下，动效层不会制造横向溢出。"""
    page = browser.new_page(viewport={"width": width, "height": 900})
    _load_admin_page(page)
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    ) is True

    if width == 375:
        page.click("[data-drawer-open]")
        page.wait_for_timeout(220)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ) is True
        assert page.locator("[data-drawer]").evaluate(
            "node => getComputedStyle(node).transform"
        ) == "matrix(1, 0, 0, 1, 0, 0)"

    page.close()


def test_no_script_fallback(browser: Browser) -> None:
    """禁用 JavaScript 后隐藏抽屉，并保留可见可点击的后备导航。"""
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
    )
    page = context.new_page()
    page.set_content(f"<style>{ADMIN_CSS}</style>{_admin_fixture()}")
    drawer = page.locator("[data-drawer]")
    fallback = page.locator(".no-script-nav")
    assert drawer.evaluate("node => getComputedStyle(node).display") == "none"
    assert fallback.evaluate("node => getComputedStyle(node).display") != "none"
    title_box = page.locator(".topbar-title").bounding_box()
    assert title_box is not None and title_box["width"] > 200
    fallback.locator("summary").click()
    fallback.locator('a[href="#fallback-target"]').click()
    assert page.evaluate("() => location.hash") == "#fallback-target"
    page.set_viewport_size({"width": 1100, "height": 844})
    assert fallback.evaluate("node => getComputedStyle(node).display") != "none"
    assert page.locator(".admin-workspace").evaluate(
        "node => getComputedStyle(node).marginLeft"
    ) == "0px"
    context.close()
