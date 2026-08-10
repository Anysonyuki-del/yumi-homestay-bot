from pathlib import Path

from homestay_bot import web

ASSET_ROOT = Path(web.__file__).resolve().parent


def test_no_javascript_navigation_reaches_every_core_admin_page() -> None:
    """脚本加载失败时，小屏导航仍须覆盖全部核心页面。"""
    layout = (ASSET_ROOT / "templates/layouts/admin.html").read_text()
    fallback = layout.split('<details class="no-script-nav">', 1)[1].split("</details>", 1)[0]

    expected_links = {
        "/employee/admin": "总览",
        "/employee/properties": "房源管理",
        "/employee/knowledge": "知识库",
        "/employee/customers": "客户管理",
        "/employee/tasks": "任务中心",
        "/employee/approvals": "预订审批",
        "/employee/admin/diagnostics": "系统诊断",
        "/employee/account": "账号安全",
    }
    for href, label in expected_links.items():
        assert f'href="{href}"' in fallback
        assert label in fallback


def test_admin_javascript_contract_covers_accessible_progressive_enhancements() -> None:
    """静态契约锁定抽屉、危险确认、脏表单和重复提交保护。"""
    script = (ASSET_ROOT / "static/admin.js").read_text()

    assert 'event.key === "Escape"' in script
    assert "focusBeforeDrawer" in script
    assert "focusBeforeDrawer.focus()" in script
    assert "window.confirm" in script
    assert 'addEventListener("beforeunload"' in script
    assert "hasUnsavedChanges" in script
    assert 'form.dataset.submitting === "true"' in script
    assert "event.preventDefault()" in script


def test_admin_css_contract_covers_mobile_first_accessibility_and_breakpoints() -> None:
    """静态契约锁定移动默认布局、四档断点和无障碍降级。"""
    css = (ASSET_ROOT / "static/app.css").read_text()

    assert "transform: translateX(-105%)" in css
    for width in (375, 768, 1024, 1440):
        assert f"@media (min-width: {width}px)" in css
    assert ":focus-visible" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "overflow-x: clip" in css or "overflow-x: hidden" in css
