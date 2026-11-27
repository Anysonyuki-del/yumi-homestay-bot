import math
import re
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
        "/employee/admin": "工作台",
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


# 双态前景/背景配对与下限：正文、按钮字、链接、状态与知识范围走 4.5:1，
# 控件边框、焦点环和主按钮相对卡片的边界走 3:1。禁用态属 WCAG 1.4.3 豁免，不列入。
_INK = ("--ink-strong", "--text", "--ink-mid", "--ink-soft", "--muted")
_CONTRAST_PAIRS = (
    *[(ink, surface, 4.5) for ink in _INK
      for surface in ("--surface", "--page", "--surface-sunk", "--sidebar-bg")],
    ("--ink-soft", "--surface-well", 4.5),
    ("--on-primary", "--primary", 4.5),
    ("--on-primary-hover", "--primary-hover", 4.5),
    ("--on-primary", "--primary-active", 4.5),
    *[(ink, surface, 4.5) for ink in ("--link", "--link-hover")
      for surface in ("--surface", "--page", "--surface-sunk", "--sidebar-bg", "--primary-soft")],
    ("--toast-ink", "--toast-bg", 4.5),
    ("--on-ink-strong", "--ink-strong", 4.5),
    ("--on-danger", "--danger", 4.5),
    *[(f"--{tone}", f"--{tone}-soft", 4.5) for tone in ("success", "warning", "danger", "info")],
    *[(f"--{tone}", "--surface", 4.5) for tone in ("success", "warning", "danger", "info")],
    *[(f"--scope-{scope}-fg", f"--scope-{scope}-bg", 4.5)
      for scope in ("global", "room", "public")],
    ("--control-border", "--surface", 3.0),
    ("--control-border", "--page", 3.0),
    ("--focus", "--surface", 3.0),
    ("--focus", "--page", 3.0),
    ("--primary", "--surface", 3.0),
)


def test_both_color_schemes_meet_wcag_contrast_on_their_own_tokens() -> None:
    """浅色与系统暗色两套令牌各自达到 WCAG 阈值。

    暗色曾隐式依赖「--surface 是白色」：轻提示、跳转链接和危险按钮悬停都拿
    --surface 当白字，暗色下直接融进底色。这里按配对表逐项实算，新增专属前景令牌
    （--toast-ink / --on-ink-strong / --on-danger）也一并纳入。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    for mode in ("light", "dark"):
        tokens = _mode_tokens(css, mode)
        for foreground, background, minimum in _CONTRAST_PAIRS:
            ratio = _contrast_ratio(
                _expand_hex(tokens[foreground]), _expand_hex(tokens[background])
            )
            assert ratio >= minimum, (
                f"{mode}: {foreground} 在 {background} 上仅 {ratio:.2f}:1，低于 {minimum}:1"
            )


def test_admin_css_declares_dual_scheme_once_and_avoids_blanket_rules() -> None:
    """color-scheme 只在 :root 声明一次；两套外壳成对声明 theme-color；不用全局粗放规则。"""
    css = (ASSET_ROOT / "static/app.css").read_text()
    assert len(re.findall(r"(?<![\w-])color-scheme:", _strip_comments(css))) == 1
    assert "color-scheme: light dark;" in _root_block(css)
    for relative_path in ("layouts/admin.html", "layouts/auth.html"):
        source = (ASSET_ROOT / "templates" / relative_path).read_text()
        assert '<meta name="color-scheme" content="light dark">' in source
        light = _mode_tokens(css, "light")["--page"]
        dark = _mode_tokens(css, "dark")["--page"]
        assert (f'<meta name="theme-color" media="(prefers-color-scheme: light)" '
                f'content="{light}">') in source
        assert (f'<meta name="theme-color" media="(prefers-color-scheme: dark)" '
                f'content="{dark}">') in source
    assert "transition: all" not in css
    assert "overflow-x: clip" not in css and "overflow-x: hidden" not in css


def test_action_fill_tokens_never_paint_foreground() -> None:
    """--primary 系只作填充与描边，绝不作文字或图标前景。

    暗色主色 #b84018 在暖炭卡片上只有约 3:1，拿它写字必然不达标；前景一律走
    --link / --link-hover。caret-color 与 accent-color 是明确例外，不在拦截范围。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    offenders = re.findall(
        r"[^{};]*(?<![\w-])color:\s*var\(--primary(?:-hover|-active)?\)", css
    )
    assert not offenders, f"前景色引用了填充用主色：{offenders}"


def test_spring_easings_follow_the_damped_oscillator_formula() -> None:
    """两条弹簧曲线必须是二阶欠阻尼振子单位阶跃响应的 20 点采样。

    手填的关键点无从核对，改一个点就悄悄变成另一条曲线；这里按公式复算。
    linear() 只能出现在 @supports 包裹里：同名变量双写不是回退，不认识 linear()
    的浏览器会让整条过渡失效，:root 主块必须保留 cubic-bezier 默认值。
    """
    css = _strip_comments((ASSET_ROOT / "static/app.css").read_text())
    condition = "@supports (transition-timing-function: linear(0, 1)) {"
    start = css.index(condition)
    end = css.index("\n}\n", start)
    block = css[start:end]
    for position in (match.start() for match in re.finditer(r"(?<![\w-])linear\(", css)):
        assert start <= position < end, "linear() 必须只出现在 @supports 包裹里"
    root = _root_block(css)
    for name, (zeta, omega) in {
        "--ease-spring-ui": (0.79, 7.5), "--ease-spring-panel": (0.93, 6.8),
    }.items():
        assert re.search(rf"{name}:\s*cubic-bezier\(", root), f"{name} 缺少旧浏览器默认值"
        match = re.search(rf"{name}:\s*linear\(([^)]*)\);", block)
        assert match, f"{name} 没有在 @supports 里升级"
        points = [float(value) for value in match.group(1).split(",")]
        assert points == _spring_points(zeta, omega), f"{name} 与公式采样不一致"


def _strip_comments(css: str) -> str:
    """去掉 CSS 注释，避免注释里提到的语法被当成真实声明。"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _spring_points(zeta: float, omega: float, count: int = 20) -> list[float]:
    """按 y(t)=1-e^(-ζωt)(cos ωd·t + ζ/√(1-ζ²)·sin ωd·t) 在 t=i/(n-1) 采样，首尾钳制 0/1。"""
    damped = omega * math.sqrt(1 - zeta ** 2)
    points = []
    for index in range(count):
        t = index / (count - 1)
        value = 1 - math.exp(-zeta * omega * t) * (
            math.cos(damped * t) + zeta / math.sqrt(1 - zeta ** 2) * math.sin(damped * t)
        )
        points.append(round(value, 3))
    points[0], points[-1] = 0.0, 1.0
    return points


def test_narrow_room_cards_switch_to_the_mobile_timeline() -> None:
    """窄卡片切换到手机版式，宽卡片保留分段甘特图。

    2026-09-16 前两侧共用 `_calendar_segments`，只是段宽不同（7 天 / 3 天）。
    现在手机改用 `_room_timeline_mobile`：不分段，跨度交给迷你条、身份交给订单卡。
    这条测试锁住「两套布局各自接到正确的宏」，避免哪天两边又被合并回同一个。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    ui = (ASSET_ROOT / "templates/components/ui.html").read_text()

    container_query = re.search(
        r"@container\s*\(max-width:\s*620px\)\s*\{(.*?)\n\}", css, re.S
    )
    assert container_query, "找不到切换布局的容器查询"
    body = container_query.group(1)
    assert ".cal__layout--wide { display: none; }" in body
    assert ".cal__layout--compact { display: block; }" in body

    assert "_calendar_segments(timeline, today, 7, featured_order_id)" in ui, (
        "宽布局必须继续使用 7 天分段的甘特图"
    )
    assert "_room_timeline_mobile(timeline, today, featured_order_id)" in ui, (
        "窄布局必须使用手机专用宏"
    )
    assert "_calendar_segments(timeline, today, 3" not in ui, (
        "手机端不应再切成 3 天一段"
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


def test_every_layout_busts_static_asset_cache_with_release_version() -> None:
    """后台与认证两套外壳都要按发布版本引用样式表。

    /static 以裸 StaticFiles 挂载，不发送 Cache-Control，浏览器会走启发式缓存，
    版本查询串是这里唯一的缓存失效手段；任一外壳漏掉都会让该页发版后吃到旧样式。
    """
    template_root = ASSET_ROOT / "templates"
    for relative_path in ("layouts/admin.html", "layouts/auth.html"):
        source = (template_root / relative_path).read_text()
        assert 'href="/static/app.css?v={{ app_version }}"' in source
        assert 'href="/static/app.css"' not in source


def test_admin_template_context_exposes_one_release_version(monkeypatch) -> None:
    """全部后台页面必须复用同一应用版本上下文。"""
    monkeypatch.setattr(web, "get_app_version", lambda: "1.2.3")
    monkeypatch.setattr(web, "get_app_version_label", lambda: "v1.2.3")

    context = web.base_template_context(object())

    assert context == {
        "app_name": "YuMi 管理后台",
        "app_version": "1.2.3",
        "app_version_label": "v1.2.3",
        # 一次性失败提示：无会话时为空串，读取后即清除。
        "page_error": "",
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


def test_no_table_disappears_on_phones() -> None:
    """`.responsive-table` 在手机上整块隐藏，用它就必须另配手机端内容。

    没有 `.mobile-card-list` 兄弟节点的表格套上这个类，手机上会一条记录也看不
    到，DOM 里却有完整数据——页面既不报错也不留空状态，只是内容凭空消失。没有
    手机端替代内容的表格应当用 `.table-scroll` 横向滚动。
    """
    template_root = ASSET_ROOT / "templates"
    offenders = [
        str(path.relative_to(template_root))
        for path in sorted(template_root.rglob("*.html"))
        if "responsive-table" in (source := path.read_text())
        and "mobile-card-list" not in source
    ]

    assert offenders == []


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


def test_archivable_statuses_have_one_definition_only() -> None:
    """可归档状态只能有一份定义，模板不得再硬编码副本。

    仓储和列表资格使用同一状态集合；真实执行一致性由仓储资格矩阵验证，
    这里只检查模板没有再定义状态副本，不绑定具体上下文变量名。
    """
    from homestay_bot.domain.enums import (
        ARCHIVABLE_TASK_STATUSES,
        BusinessTaskStatus,
    )

    assert set(ARCHIVABLE_TASK_STATUSES) == {
        BusinessTaskStatus.COMPLETED,
        BusinessTaskStatus.CANCELLED,
        BusinessTaskStatus.EXPIRED,
    }

    template_root = ASSET_ROOT / "templates"
    for relative_path in ("tasks/index.html", "tasks/detail.html"):
        source = (template_root / relative_path).read_text()
        assert '"completed", "cancelled", "expired"' not in source, (
            f"{relative_path} 又硬编码了可归档状态副本"
        )


def test_room_card_column_threshold_can_hold_the_default_calendar() -> None:
    """房间卡片并排的最小列宽必须装得下默认日历，否则日期会被滚动切断。

    这个问题复发过两次：卡片并排阈值定得比日历还窄时，日历被压到最小列宽仍溢出，
    只能横向滚动、切掉末尾日期，默认视图就「格子不可读」。算式固定：默认 3 天视图
    有 6 个日期列、每列最小 104px = 624px，加卡片左右内边距（各 22px）= 668px。
    并排阈值低于这个数就必然重现，因此在这里钉住。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()

    rules = re.findall(r"\.room-operations-list \{([^}]*)\}", css)
    assert rules, "找不到 .room-operations-list 规则"
    body = "".join(rules)
    thresholds = [int(value) for value in re.findall(r"minmax\(min\(100%,\s*(\d+)px", body)]
    if thresholds:
        assert all(value >= 668 for value in thresholds), (
            f"并排阈值 {thresholds} 小于日历所需的 668px，日期会被横向滚动切断"
        )
    # 长跨度（7 天及以上）固定单栏：10–17 个日期列并排必然滚动。
    assert ".room-operations-list--wide" in css


def test_calendar_grid_lines_stay_visible_against_the_track() -> None:
    """日历网格线与迷你轨未来段在浅、暗两套轨道底上都必须能分辨出来。

    这个问题出现过两次：表头竖线与行程分隔线都用过 #F1F5F9，在白底上对比度只有
    约 1.10:1，肉眼等同于没有线，网格结构因此读不出来（用户原话「边框和背景融在
    一起了」）。暗色轨道换成暖炭后同样要守住 1.2:1；迷你轨未来段曾经只有约 1.1:1。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    block = _room_card_block(css)

    # 线条现在直接引用全局中性令牌，因此连令牌本身一起校验：--line 调浅，
    # 日历网格同样会消失，这个下限必须扎在被真正使用的那个值上。
    grid_rules = re.findall(r"\.cal__(?:content|dates|tracks) \{([^}]*)\}", block)
    assert grid_rules, "找不到日历网格规则"
    used = set()
    for rule in grid_rules:
        # 只取线条属性：同一条规则里的 background 是面，参照系不同，混进来会误判。
        used |= set(re.findall(r"border[\w-]*:[^;]*?var\((--[\w-]+)\)", rule))
        for gradient in re.findall(r"repeating-linear-gradient\(([^;]*)\)", rule):
            used |= set(re.findall(r"var\((--[\w-]+)\)", gradient))
    # 竖线渐变里同时有 --days 这类几何变量，按「能解析成颜色」筛掉；令牌是否存在
    # 由 test_room_card_colors_all_go_through_design_tokens 单独守。
    used = {name for name in used if name in _root_tokens(css)}
    assert used, "日历网格线没有走令牌，改一次要翻遍全表"

    fill = re.search(r"\.cal-m__fill--future \{[^}]*background: var\((--[\w-]+)\)", block)
    lane = re.search(r"\.cal-m__lane \{[^}]*background: var\((--[\w-]+)\)", block)
    assert fill and lane, "找不到迷你轨未来段或轨道底规则"
    for mode in ("light", "dark"):
        tokens = {**_mode_tokens(css, mode), **_calendar_tokens(css, mode)}
        track = _expand_hex(tokens["--surface"])
        for name in sorted(used):
            ratio = _contrast_ratio(_expand_hex(tokens[name]), track)
            assert ratio >= 1.2, f"{mode}: {name} 相对轨道仅 {ratio:.2f}:1，线条会融进背景"
        ratio = _contrast_ratio(tokens[fill.group(1)], _expand_hex(tokens[lane.group(1)]))
        assert ratio >= 1.2, f"{mode}: 迷你轨未来段相对轨道仅 {ratio:.2f}:1"


def _root_tokens(css: str) -> dict[str, str]:
    """取出 :root 里的字面色令牌，用于解析日历规则里的 var() 引用。"""
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{3,6})", _root_block(css)))


def _root_block(css: str) -> str:
    return css[css.index(":root {"):css.index("\n}", css.index(":root {"))]


def _dark_root_block(css: str) -> str:
    """系统暗色下覆盖 :root 的那一块（@media (prefers-color-scheme: dark) 内的 :root）。"""
    start = css.index("@media (prefers-color-scheme: dark) {\n  :root {")
    return css[start:css.index("\n  }\n}", start)]


def _mode_tokens(css: str, mode: str) -> dict[str, str]:
    """浅色取 :root 字面色；暗色在其上叠加暗色块，未覆盖的令牌沿用浅色值。"""
    tokens = _root_tokens(css)
    if mode == "dark":
        tokens.update(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{3,6})", _dark_root_block(css)))
    return tokens


def _calendar_main_block(css: str) -> str:
    """日历主块 `.cal { ... }`：只允许 var() 与 color-mix()。"""
    start = css.index("\n.cal {\n")
    return css[start:css.index("\n}\n", start)]


def _calendar_fallbacks(css: str) -> tuple[int, dict[str, str], dict[str, str]]:
    """解析 @supports not (color-mix) 回退块，返回块位置与浅/暗两组静态值。"""
    start = css.index("@supports not (color: color-mix(in srgb, red 50%, blue)) {")
    block = css[start:css.index("\n}\n", start)]
    light, dark = block.split("@media (prefers-color-scheme: dark) {", 1)
    pattern = r"(--cal-[\w-]+):\s*(#[0-9A-Fa-f]{6});"
    return start, dict(re.findall(pattern, light)), dict(re.findall(pattern, dark))


def _calendar_tokens(css: str, mode: str) -> dict[str, str]:
    """按日历主块的 var() / color-mix() 把 --cal-* 解析成该模式下的实际颜色。"""
    tokens = _mode_tokens(css, mode)
    resolved = {}
    for name, value in re.findall(r"(--cal-[\w-]+):\s*([^;]+);", _calendar_main_block(css)):
        mix = re.fullmatch(
            r"color-mix\(in srgb, var\((--[\w-]+)\) (\d+)%, var\((--[\w-]+)\)\)", value
        )
        alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
        if mix:
            top, percent, bottom = mix.groups()
            resolved[name] = _srgb_mix(tokens[top], tokens[bottom], float(percent))
        elif alias:
            resolved[name] = _expand_hex(tokens[alias.group(1)])
        else:
            raise AssertionError(f"{name} 不是 var() 或 color-mix()：{value}")
    return resolved


def _room_card_block(css: str) -> str:
    """房间卡片与日历那段 CSS：本次令牌化的范围，也是字面色的禁区。"""
    start = css.index("/* 房间摘要与日期轴")
    end = css.index("@media (max-width: 520px) {\n  .room-operation-card { padding")
    return css[start:end]


def _expand_hex(value: str) -> str:
    """把 #fff 这类缩写补成六位，令牌两种写法都要能参与计算。"""
    digits = value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return f"#{digits}"


def _srgb_mix(top: str, bottom: str, percent: float) -> str:
    """按 color-mix(in srgb, top P%, bottom) 的线性插值算出等价字面色。"""
    top, bottom = _expand_hex(top), _expand_hex(bottom)
    parts = [
        (int(top[index:index + 2], 16) * percent / 100
         + int(bottom[index:index + 2], 16) * (100 - percent) / 100)
        for index in (1, 3, 5)
    ]
    return "#" + "".join(f"{round(value):02x}" for value in parts)


def test_room_card_colors_all_go_through_design_tokens() -> None:
    """房间卡片不得自带一套写死的配色，颜色必须经过令牌。

    v1.23.0 重设计时这一段把 --primary、--muted 等换成了邻近的字面色，页面因此
    长出了第二套强调色：控制台其它页面按 --primary 走，这一页按 #3448a5 走，换主
    色要逐处翻。唯一允许出现字面色的地方是 `@supports not (color-mix)` 回退块——
    那是一个可枚举的声明缝，不是散落的用法；`.cal` 主块本身也不许写字面色。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    block = _room_card_block(css)

    seam = re.search(r"@supports not \(color: color-mix\([^)]*\)\) \{.*?\n\}\n", block, re.S)
    assert seam, "找不到日历的 @supports not 回退块"
    without_seam = block.replace(seam.group(0), "")
    leaked = re.findall(r"#[0-9A-Fa-f]{3,6}|\brgba?\(", without_seam)
    assert not leaked, f"房间卡片里仍有绕过令牌的字面色：{leaked}"
    assert "color: white" not in without_seam, "白色也要走 --surface，否则暗色方案无从下手"

    # 引用到的令牌必须真存在；var(--typo) 只会静默变成透明或继承。
    declared = set(re.findall(r"(--[\w-]+):", _root_block(css)))
    declared |= set(re.findall(r"(--[\w-]+):", block))  # .cal 与 .cal__segment 作用域
    # 这两个由模板按每段的天数与条数写在 style 属性里，CSS 侧不声明。
    # --preview-rows 是设计常量（固定三条），已回到 .cal__segment 里声明。
    declared |= {"--days", "--rows"}
    ui = (ASSET_ROOT / "templates/components/ui.html").read_text()
    for name in {"--days", "--rows"}:
        assert f"{name}:" in ui, f"{name} 已不再由模板写入，CSS 里的 var() 会失效"
    for name in set(re.findall(r"var\((--[\w-]+)", block)):
        assert name in declared, f"{name} 既不在 :root 也不在这段的作用域里"


def test_calendar_bar_palette_stays_derived_from_the_primary_token() -> None:
    """日历条配色跟着 --primary 与 --warning 走，旧浏览器回退值在浅/暗两态都与 color-mix 等价。

    早先「同名属性先写字面色、再写 color-mix」的双写回退从未生效：自定义属性不在
    解析期校验，后一条恒覆盖前一条，不认识 color-mix 的 Safari 15.4–16.1 在计算期
    整条失效。回退改放 @supports not 块，并为暗色嵌套一组；只改其中一处，两类
    浏览器就会看到两套配色，而且没有任何地方会报错。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    main = _calendar_main_block(css)
    assert not re.findall(r"#[0-9A-Fa-f]{3,6}|\brgba?\(", main), "日历主块不得写字面色"
    mixes = re.findall(
        r"(--cal-[\w-]+):\s*color-mix\(in srgb, var\((--[\w-]+)\) (\d+)%, var\((--[\w-]+)\)\);",
        main,
    )
    assert len(mixes) >= 4, f"日历主块缺少 color-mix 派生：{mixes}"

    position, light, dark = _calendar_fallbacks(css)
    anchor = css.index("@media (max-width: 520px) {\n  .room-operation-card { padding")
    assert css.index("\n.cal {\n") < position < anchor, "回退块必须紧跟日历主块、位于房间卡片段内"
    names = {name for name, *_ in mixes}
    assert set(light) == names and set(dark) == names, (
        "回退块必须覆盖每个 color-mix 令牌，浅暗各一组"
    )
    for mode, fallbacks in (("light", light), ("dark", dark)):
        tokens = _mode_tokens(css, mode)
        for name, top, percent, bottom in mixes:
            assert top in {"--primary", "--warning"}, (
                f"{name} 挂在 {top} 上，既不是主色也不是告警色，换令牌时不会跟着走"
            )
            expected = _srgb_mix(tokens[top], tokens[bottom], float(percent))
            assert fallbacks[name].lower() == expected, (
                f"{mode}: {name} 回退值 {fallbacks[name]} 与 color-mix 结果 {expected} 不一致"
            )
        resolved = {**tokens, **_calendar_tokens(css, mode)}
        for ink, surface in (("--cal-current-ink", "--cal-current-bg"),
                             ("--cal-future-ink", "--cal-future-bg")):
            ratio = _contrast_ratio(_expand_hex(resolved[ink]), _expand_hex(resolved[surface]))
            assert ratio >= 4.5, f"{mode}: {ink} 在 {surface} 上仅 {ratio:.2f}:1"


# ---- 结构守卫（前端审查 2026-11-25）：同类问题已逐个修过，这里按类拦截，不再靠逐处复查。 ----

_TEMPLATE_ROOT = ASSET_ROOT / "templates"


def _templates() -> list[Path]:
    return sorted(_TEMPLATE_ROOT.rglob("*.html"))


def test_templates_format_times_through_the_wuhan_timezone_filters() -> None:
    """模板直接 .strftime 会按 UTC 显示（F-02 的两处就是这样差了 8 小时）。"""
    offenders = [
        str(path.relative_to(_TEMPLATE_ROOT))
        for path in _templates()
        if ".strftime(" in path.read_text()
    ]
    assert not offenders, f"请改用 datetime_zh / date_zh：{offenders}"


def test_checkbox_labels_declare_a_defined_layout_class() -> None:
    """裸 <label> 包勾选框会套上文本框的块级标签样式，勾选框和文字错位（F-03）。"""
    css = (ASSET_ROOT / "static/app.css").read_text()
    label_with_choice = re.compile(
        r"<label\b([^>]*)>(?:(?!</label>).)*?type=\"(?:checkbox|radio)\"", re.S
    )
    missing, undefined = [], set()
    for path in _templates():
        for match in label_with_choice.finditer(path.read_text()):
            classes = re.search(r'class="([^"]*)"', match.group(1))
            if not classes:
                missing.append(str(path.relative_to(_TEMPLATE_ROOT)))
                continue
            if not any(
                re.search(rf"\.{re.escape(name)}(?![\w-])", css)
                for name in classes.group(1).split()
            ):
                undefined.add(classes.group(1))
    assert not missing, f"勾选标签缺少布局类：{missing}"
    assert not undefined, f"勾选标签用的类在 app.css 里没有样式：{undefined}"


def test_every_css_custom_property_used_is_declared() -> None:
    """var(--ink) 这类拼错的令牌不会报错，只会静默失效（F-07）。"""
    css = (ASSET_ROOT / "static/app.css").read_text()
    declared = set(re.findall(r"(--[\w-]+)\s*:", css))
    # 由模板 style 属性或脚本运行时写入，CSS 侧不声明。
    runtime = {"--days", "--rows", "--bulk-toolbar-height"}
    ui = (ASSET_ROOT / "templates/components/ui.html").read_text()
    script = (ASSET_ROOT / "static/admin.js").read_text()
    assert "--days:" in ui and "--rows:" in ui
    assert '"--bulk-toolbar-height"' in script
    undeclared = set(re.findall(r"var\((--[\w-]+)", css)) - declared - runtime
    assert not undeclared, f"引用了未声明的变量：{sorted(undeclared)}"


def test_page_alerts_have_a_single_owner() -> None:
    """同一条提示被布局和子模板各渲染一次，读屏会念两遍（F-04、N-02）。

    notice、page_error、flash 只由布局渲染；子模板若就地显示 error，必须设
    layout_error_inline 让布局跳过，否则同一条 error 出现两次。
    """
    layout_only = re.compile(r"\{\{\s*(notice|page_error|flash)\b")
    inline_error = re.compile(r"\{\{\s*error\s*\}\}")
    duplicated, unclaimed = [], []
    for path in _templates():
        name = str(path.relative_to(_TEMPLATE_ROOT))
        if name.startswith("layouts/"):
            continue
        text = path.read_text()
        if layout_only.search(text):
            duplicated.append(name)
        if inline_error.search(text) and "layout_error_inline" not in text:
            unclaimed.append(name)
    assert not duplicated, f"子模板不得再渲染布局提示：{duplicated}"
    assert not unclaimed, f"就地显示 error 却没接管布局提示：{unclaimed}"
    layout = (_TEMPLATE_ROOT / "layouts/admin.html").read_text()
    assert "{% if error and not layout_error_inline %}" in layout


def test_status_colors_never_borrow_the_action_color() -> None:
    """房间状态的点和占比条不得使用主操作色。

    陶红是「这里可以点」的唯一信号；状态色借用它，员工会把只读的状态当成可操作项，
    或把「已入住」读成告警。状态只能用中性、成功、告警、危险这些语义色。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    rules = re.findall(r"([^{}]*\.(?:status-dot|readiness-bar__seg)--[\w-]+[^{}]*)\{([^}]*)\}", css)
    assert rules, "没有找到状态色规则，选择器可能改名，需同步本守卫"
    # --focus 与主色同值、--link 是同色系，借用它们等于换名借用主色。
    action_tokens = ("var(--primary", "var(--focus", "var(--link")
    borrowed = [
        sel.strip() for sel, body in rules if any(token in body for token in action_tokens)
    ]
    assert not borrowed, f"状态色借用了主操作色：{borrowed}"
