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
    """静态契约锁定抽屉、危险确认、脏表单和可感知提交状态。"""
    script = (ASSET_ROOT / "static/admin.js").read_text()

    assert 'event.key === "Escape"' in script
    assert 'classList.add("js-enabled")' in script
    assert "syncDrawerAccessibility" in script
    assert "drawer.inert = !shouldExpose" in script
    assert "focusBeforeDrawer" in script
    assert "focusBeforeDrawer.focus()" in script
    assert "window.confirm" in script
    assert 'form[data-confirm], form[data-danger-confirm]' in script
    assert 'addEventListener("beforeunload"' in script
    assert "const dirtyForms = new Set();" in script
    assert "dirtyForms.add(form)" in script
    assert "dirtyForms.delete(form)" in script
    assert "dirtyForms.size === 0" in script
    assert 'form.dataset.submitting === "true"' in script
    assert "setSubmittingState" in script
    assert 'submitter.dataset.originalLabel' in script
    assert 'submitter.textContent = "正在处理…"' in script
    assert 'submitter.setAttribute("aria-busy", "true")' in script
    assert 'form.setAttribute("aria-busy", "true")' in script
    assert "event.preventDefault()" in script
    assert 'workspace.setAttribute("aria-hidden", "true")' in script
    assert "workspace.inert = true" in script
    assert 'document.body.classList.add("drawer-is-open")' in script
    assert 'workspace.removeAttribute("aria-hidden")' in script
    assert "workspace.inert = false" in script
    assert 'document.body.classList.remove("drawer-is-open")' in script
    assert 'window.matchMedia("(min-width: 1024px)")' in script
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)")' in script
    assert "drawerTransitionToken" in script
    assert "finishDrawerClose" in script
    assert 'addEventListener("transitionend"' in script
    assert "requestAnimationFrame" in script
    assert 'submitter.classList.add("is-submitting")' in script
    assert "if (!event.defaultPrevented) dirtyForms.delete(form);" in script


def test_admin_css_contract_covers_mobile_first_accessibility_and_breakpoints() -> None:
    """静态契约锁定移动默认布局、四档断点和无障碍降级。"""
    css = (ASSET_ROOT / "static/app.css").read_text()

    assert "transform: translateX(-105%)" in css
    for width in (375, 768, 1024, 1440):
        assert f"@media (min-width: {width}px)" in css
    assert "--primary: #2563eb" in css
    assert "--sidebar-width: 176px" in css
    assert "--topbar-height: 56px" in css
    assert ":focus-visible { outline: 3px solid var(--primary)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "--motion-fast: 160ms" in css
    assert "--motion-panel: 180ms" in css
    assert "@keyframes page-enter" in css
    assert "@keyframes status-enter" in css
    assert "@keyframes submit-spin" in css
    assert ".drawer-backdrop.is-visible" in css
    assert "button.is-submitting::before" in css
    assert "transition: all" not in css
    reduced_motion = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "animation: none !important" in reduced_motion
    assert "transition: none !important" in reduced_motion
    assert "body {" in css and "overflow-x: clip" not in css and "overflow-x: hidden" not in css
    assert "overflow-wrap: anywhere" in css
    assert "pre, code" in css
    assert "overflow: auto" in css
    assert "white-space: pre-wrap" in css
    assert "overscroll-behavior" in css
    assert _contrast_ratio("#2563eb", "#f7f8fa") >= 3
    assert _contrast_ratio("#2563eb", "#ffffff") >= 3
    assert ".data-table" in css
    assert ".app-version" in css
    assert "font-variant-numeric: tabular-nums" in css
    assert ".page-content > .panel + .panel" in css
    assert "detail-section + .detail-section" in css
    assert " .panel + .panel" not in css.replace(
        ".page-content > .panel + .panel", ""
    )


def test_admin_shell_uses_grouped_lightweight_navigation() -> None:
    """桌面后台应使用分组导航和唯一页面标题，避免重复标题占用首屏。"""
    layout = (ASSET_ROOT / "templates/layouts/admin.html").read_text()

    assert 'class="nav-group"' in layout
    assert "运营" in layout
    assert "客户与内容" in layout
    assert "系统管理" in layout
    assert "topbar__eyebrow" not in layout
    assert layout.count("{{ page_title }}") == 2  # title 元素与唯一可见 h1
    assert 'class="app-version"' in layout
    assert "{{ app_version_label }}" in layout
    assert 'href="/static/app.css?v={{ app_version }}"' in layout
    assert 'src="/static/admin.js?v={{ app_version }}"' in layout


def test_admin_template_context_exposes_one_release_version(monkeypatch) -> None:
    """全部后台页面必须复用同一应用版本上下文。"""
    monkeypatch.setattr(web, "get_app_version", lambda: "1.2.3")
    monkeypatch.setattr(web, "get_app_version_label", lambda: "v1.2.3")

    context = web.base_template_context(object())

    assert context == {
        "app_name": "YuMi 管理后台",
        "app_version": "1.2.3",
        "app_version_label": "v1.2.3",
    }


def test_core_list_templates_share_desktop_table_and_mobile_card_patterns() -> None:
    """核心运营列表应统一桌面扫描方式，同时保留移动卡片。"""
    template_root = ASSET_ROOT / "templates"
    for relative_path in (
        "customers/index.html",
        "properties/index.html",
        "tasks/index.html",
        "approvals/index.html",
    ):
        source = (template_root / relative_path).read_text()
        assert 'class="data-table"' in source
        assert "mobile-card-list" in source


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
