from pathlib import Path

from homestay_bot import web

ASSET_ROOT = Path(web.__file__).resolve().parent


def _relative_luminance(hex_color: str) -> float:
    """按 WCAG 2.1 把不透明十六进制颜色转换为相对亮度。"""
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    """计算两个不透明颜色的 WCAG 对比度。"""
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_no_javascript_navigation_reaches_every_core_admin_page() -> None:
    """脚本加载失败时，小屏导航仍须覆盖全部核心页面。"""
    layout = (ASSET_ROOT / "templates/layouts/admin.html").read_text()
    assert 'data-drawer inert aria-hidden="true"' in layout
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
    assert 'classList.add("js-enabled")' in script
    assert "syncDrawerAccessibility" in script
    assert "drawer.inert = !shouldExpose" in script
    assert "focusBeforeDrawer" in script
    assert "focusBeforeDrawer.focus()" in script
    assert "window.confirm" in script
    assert 'addEventListener("beforeunload"' in script
    assert "const dirtyForms = new Set();" in script
    assert "dirtyForms.add(form)" in script
    assert "dirtyForms.delete(form)" in script
    assert "dirtyForms.size === 0" in script
    assert 'form.dataset.submitting === "true"' in script
    assert "event.preventDefault()" in script
    assert 'workspace.setAttribute("aria-hidden", "true")' in script
    assert "workspace.inert = true" in script
    assert 'document.body.classList.add("drawer-is-open")' in script
    assert 'workspace.removeAttribute("aria-hidden")' in script
    assert "workspace.inert = false" in script
    assert 'document.body.classList.remove("drawer-is-open")' in script
    assert 'window.matchMedia("(min-width: 1024px)")' in script
    assert "if (!event.defaultPrevented) dirtyForms.delete(form);" in script


def test_admin_css_contract_covers_mobile_first_accessibility_and_breakpoints() -> None:
    """静态契约锁定移动默认布局、四档断点和无障碍降级。"""
    css = (ASSET_ROOT / "static/app.css").read_text()

    assert "transform: translateX(-105%)" in css
    for width in (375, 768, 1024, 1440):
        assert f"@media (min-width: {width}px)" in css
    assert ":focus-visible { outline: 3px solid #1d4ed8" in css
    assert ".admin-sidebar :focus-visible { outline-color: var(--gold)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "body {" in css and "overflow-x: clip" not in css and "overflow-x: hidden" not in css
    assert "overflow-wrap: anywhere" in css
    assert "pre, code" in css
    assert "overflow: auto" in css
    assert "white-space: pre-wrap" in css
    assert "overscroll-behavior" in css
    assert _contrast_ratio("#1d4ed8", "#f8fafc") >= 3
    assert _contrast_ratio("#1d4ed8", "#ffffff") >= 3
    # 侧栏链接和关闭按钮位于深海军蓝背景，必须使用独立不透明焦点色。
    assert _contrast_ratio("#ca8a04", "#172554") >= 3
    assert ".page-content > .panel + .panel" in css
    assert "detail-section + .detail-section" in css
    assert " .panel + .panel" not in css.replace(
        ".page-content > .panel + .panel", ""
    )


def test_business_templates_extend_one_admin_shell() -> None:
    """真实业务页只能继承统一后台，不能重复 meta、样式或脚本标签。"""
    template_root = ASSET_ROOT / "templates"
    relative_paths = (
        "tasks/index.html",
        "tasks/detail.html",
        "properties/index.html",
        "properties/detail.html",
        "knowledge/index.html",
        "knowledge/detail.html",
        "customers/index.html",
        "customers/detail.html",
        "customers/merge.html",
        "approvals/index.html",
        "approvals/detail.html",
        "complaints/edit.html",
    )

    for relative_path in relative_paths:
        source = (template_root / relative_path).read_text()
        assert source.lstrip().startswith('{% extends "layouts/admin.html" %}')
        assert "<html" not in source
        assert '<script src="/static/admin.js"' not in source
        assert '<link rel="stylesheet" href="/static/app.css">' not in source
