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


def test_admin_css_keeps_contrast_and_avoids_blanket_rules() -> None:
    """主色对比度够用，不用 transition: all，页面不靠隐藏横向溢出掩盖排版问题。

    抽屉、提交状态、减少动效等交互由 tests/browser 在真实浏览器里验证；这里只留
    浏览器测不到的计算与全局约束，不再逐条复述样式源码。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()

    assert _contrast_ratio("#c24322", "#f5f2ed") >= 4.5
    assert _contrast_ratio("#c24322", "#ffffff") >= 4.5
    assert "transition: all" not in css
    assert "overflow-x: clip" not in css and "overflow-x: hidden" not in css


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


def test_calendar_grid_lines_stay_visible_against_the_white_track() -> None:
    """日历网格线必须能从白色轨道背景里分辨出来。

    这个问题出现过两次：表头竖线与行程分隔线都用过 #F1F5F9，在白底上对比度只有
    约 1.10:1，肉眼等同于没有线，网格结构因此读不出来（用户原话「边框和背景融在
    一起了」）。这里对日历自己的线条变量设下限：相对白色至少 1.2:1。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()

    root = _root_tokens(css)
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
    used = {name for name in used if name in root}
    assert used, "日历网格线没有走令牌，改一次要翻遍全表"

    white = _relative_luminance("#FFFFFF")
    for name in sorted(used):
        value = root.get(name)
        assert value, f"{name} 不在 :root 里，var() 会静默失效"
        ratio = (white + 0.05) / (_relative_luminance(_expand_hex(value)) + 0.05)
        assert ratio >= 1.2, f"{name}={value} 相对白底仅 {ratio:.2f}:1，线条会融进背景"


def _root_tokens(css: str) -> dict[str, str]:
    """取出 :root 里的字面色令牌，用于解析日历规则里的 var() 引用。"""
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{3,6})", _root_block(css)))


def _root_block(css: str) -> str:
    return css[css.index(":root {"):css.index("\n}", css.index(":root {"))]


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
    色要逐处翻。唯一允许出现字面色的地方是 `.cal` 里的 color-mix 回退声明——那是
    一个可枚举的声明缝，不是散落的用法。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    block = _room_card_block(css)

    # 先摘掉 --cal-* 声明行，剩下的任何字面色都是绕过令牌的用法。
    without_seam = re.sub(r"\s*--cal-[\w-]+:[^;]*;", "", block)
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
    """日历条配色必须跟着 --primary 与 --warning 走，回退值不得与 color-mix 脱钩。

    回退声明是给不支持 color-mix 的浏览器兜底的，两行写的必须是同一个颜色；只改
    其中一行，两类浏览器就会看到两套配色，而且没有任何地方会报错。
    """
    css = (ASSET_ROOT / "static/app.css").read_text()
    root = _root_tokens(css)
    pairs = re.findall(
        r"(--cal-[\w-]+):\s*(#[0-9A-Fa-f]{6});\s*\1:\s*"
        r"color-mix\(in srgb, var\((--[\w-]+)\) (\d+)%, var\((--[\w-]+)\)\);",
        css,
    )
    assert len(pairs) >= 4, f"color-mix 与回退值没有成对声明：{pairs}"
    for name, fallback, top, percent, bottom in pairs:
        assert top in {"--primary", "--warning"}, (
            f"{name} 挂在 {top} 上，既不是主色也不是告警色，换令牌时不会跟着走"
        )
        expected = _srgb_mix(root[top], root[bottom], float(percent))
        assert fallback.lower() == expected, (
            f"{name} 回退值 {fallback} 与 color-mix 结果 {expected} 不一致"
        )


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
