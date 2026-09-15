from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

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
            <form id="filter-form" method="get" action="/employee/tasks" data-filter-form>
              <select name="status_filter"><option value="" selected>全部状态</option></select>
              <select name="task_type"><option value="cleaning" selected>保洁</option></select>
              <input name="service_date" value="">
              <button type="submit">筛选</button>
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


def test_filter_form_removes_empty_query_values(browser: Browser) -> None:
    """筛选表单只把有效值写入 URL，空控件交给服务端默认筛选。"""
    page = browser.new_page()
    _load_admin_page(page)

    values = page.evaluate(
        """() => Object.fromEntries(
          new FormData(document.querySelector('#filter-form')).entries()
        )"""
    )

    assert values == {"task_type": "cleaning"}
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
        # 等抽屉真的停下，而不是睡一个固定时长：过渡是 --motion-panel 180ms，
        # 原来的 220ms 只留 40 毫秒余量，机器一忙就会抓到动画中间的那一帧。
        page.wait_for_function(
            "() => getComputedStyle(document.querySelector('[data-drawer]'))"
            ".transform === 'matrix(1, 0, 0, 1, 0, 0)'"
        )
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ) is True

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


def _selection_fixture() -> str:
    """返回任务列表的双布局勾选结构：桌面表格与手机卡片各一份同名勾选框。"""
    return """<!doctype html>
    <html lang="zh-CN"><head></head><body class="admin-body">
      <main class="page-content">
        <form id="selection-form" method="post" action="/employee/tasks/archive-selected"
              data-confirm="确定把勾选的任务移入归档吗？归档后不再出现在默认列表，可在「已归档」中恢复。">
          <div class="responsive-table"><table class="data-table">
            <thead><tr>
              <th scope="col" class="select-cell">
                <input type="checkbox" data-select-all aria-label="全选本页">
              </th><th scope="col">任务</th>
            </tr></thead>
            <tbody>
              <tr><td class="select-cell">
                <input type="checkbox" name="task_ids" value="11" aria-label="选择任务 11">
              </td><td>保洁</td></tr>
              <tr><td class="select-cell">
                <input type="checkbox" name="task_ids" value="12" aria-label="选择任务 12">
              </td><td>维修</td></tr>
            </tbody>
          </table></div>
          <ul class="mobile-card-list clean-list">
            <li><label class="card-select">
              <input type="checkbox" name="task_ids" value="11"><span>选择</span>
            </label></li>
            <li><label class="card-select">
              <input type="checkbox" name="task_ids" value="12"><span>选择</span>
            </label></li>
          </ul>
          <button type="submit">归档勾选的任务</button>
          <button type="submit"
                  formaction="/employee/tasks/assign-selected"
                  data-confirm="确定把勾选的 {n} 条任务分派给「{employee}」吗？"
                  >分派勾选的任务</button>
          <button type="submit"
                  formaction="/employee/tasks/other-action">没有自带文案的动作</button>
          <select name="assigned_employee_id">
            <option value="">选择员工…</option>
            <option value="3" selected>阿姨</option>
          </select>
          <button class="button button--danger" type="submit"
                  data-typed-confirm="永久删除">永久删除勾选的任务</button>
          <input type="hidden" name="confirm_count" value="0" data-confirm-count>
        </form>
      </main>
    </body></html>"""


def _load_selection_page(page: Page) -> None:
    """载入真实 CSS 与 JavaScript 的双布局勾选页面，并拦截真实提交。"""
    page.set_content(_selection_fixture())
    page.add_style_tag(content=ADMIN_CSS)
    page.add_script_tag(content=ADMIN_SCRIPT)
    page.evaluate(
        """() => {
          window.confirmCalls = [];
          window.promptCalls = [];
          window.confirm = (message) => { window.confirmCalls.push(message); return true; };
          document.getElementById("selection-form")
            .addEventListener("submit", (event) => event.preventDefault());
        }"""
    )


def _submitted_task_ids(page: Page) -> list[str]:
    """返回浏览器真正会提交的任务编号，禁用副本不计入。"""
    return page.evaluate(
        """() => new FormData(document.getElementById("selection-form"))
              .getAll("task_ids")"""
    )


def test_select_all_never_selects_an_invisible_duplicate(browser: Browser) -> None:
    """全选只能选中当前可见的那一份勾选框。

    桌面表格与手机卡片渲染了两份同名勾选框，全选如果无差别勾上两份，提交的编号
    会翻倍，页面上却看不出任何异常。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)

    page.check("[data-select-all]")

    assert _submitted_task_ids(page) == ["11", "12"]
    page.close()


def test_unchecking_a_visible_box_also_drops_its_hidden_copy(browser: Browser) -> None:
    """取消一条可见勾选后，这条任务不能再被提交。

    这是最危险的一种：用户明确取消了某条任务，页面显示未勾选，隐藏副本却仍然
    带着它进入批量归档或永久删除。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)

    page.check("[data-select-all]")
    page.uncheck('.responsive-table input[name="task_ids"][value="11"]')

    assert _submitted_task_ids(page) == ["12"]
    page.close()


def test_selection_survives_a_layout_switch_without_duplicating(
    browser: Browser,
) -> None:
    """切换到手机宽度后，已有选择被继承，且不会变成两份。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)
    page.check('.responsive-table input[name="task_ids"][value="11"]')

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_function(
        """() => document
             .querySelector('.mobile-card-list input[name="task_ids"][value="11"]')
             .disabled === false"""
    )

    assert _submitted_task_ids(page) == ["11"]
    assert page.is_checked('.mobile-card-list input[name="task_ids"][value="11"]')
    page.close()


def test_typed_confirm_counts_what_the_user_can_see(browser: Browser) -> None:
    """手输确认的条数必须等于可见选择数，而不是两套布局的总和。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)
    page.evaluate(
        """() => {
          window.prompt = (message) => { window.promptCalls.push(message); return "2"; };
        }"""
    )

    page.check("[data-select-all]")
    page.click("button[data-typed-confirm]")

    assert len(page.evaluate("() => window.promptCalls")) == 1
    assert "2 条" in page.evaluate("() => window.promptCalls[0]")
    assert page.input_value("[data-confirm-count]") == "2"
    page.close()


def test_permanent_delete_never_shows_the_archive_recoverable_wording(
    browser: Browser,
) -> None:
    """永久删除不得沿用归档表单「可以恢复」的确认文案。

    删除按钮借用归档表单提交，表单级确认写的是「可在「已归档」中恢复」；它在
    专属确认之后弹出，等于用一句「可以恢复」盖住了不可逆的真相。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)
    page.evaluate("""() => { window.prompt = () => "1"; }""")

    page.check('.responsive-table input[name="task_ids"][value="11"]')
    page.click("button[data-typed-confirm]")

    assert page.evaluate("() => window.confirmCalls") == []
    page.close()


def _timeline_fixture(
    day_count: int,
    *,
    crossing: bool = False,
    sparse_tail: bool = False,
    segment_days: int = 7,
) -> str:
    """用真实宏与合成订单验证分段、独立分轨及无脚本展开。

    `segment_days` 必须与被测布局的分段大小一致：桌面 `.cal__layout--wide`
    用 7 天。手机端自 2026-09-16 起改用 `_room_timeline_mobile`（不分段），
    因此分段相关的断言只对桌面成立。
    """
    from datetime import date, timedelta
    from types import SimpleNamespace

    from jinja2 import Environment, FileSystemLoader

    def replace_span(bar: SimpleNamespace, left: float, width: float) -> SimpleNamespace:
        bar.left_pct, bar.width_pct = left, width
        return bar

    env = Environment(loader=FileSystemLoader(PROJECT_ROOT / "src/homestay_bot/templates"))
    env.filters["status_zh"] = str
    start = date(2026, 9, 8)
    days = [SimpleNamespace(local_date=start + timedelta(days=i)) for i in range(day_count)]
    bars = [SimpleNamespace(
        order_id=str(i), customer_id=i + 1, guest_name=f"示例客人{i + 1}",
        nights=day_count, left_pct=0, width_pct=100, lane=0,
        left_continues=False, right_continues=False, checkout_verified=False,
        semantic="future", overlaps=False,
    ) for i in range(5)]
    if sparse_tail:
        # 首段三笔、末段一笔：分段后各段笔数必然不同。
        span = 100 / day_count
        bars = [
            replace_span(bar, day * span, span)
            for bar, day in zip(bars[:4], (0, 1, 2, day_count - 1), strict=True)
        ]
    if crossing:
        # 跨越段边界：在边界前一天 15:00 起、次日 12:00 止，
        # 于边界两侧分别保留 9 与 12 小时。
        bars = bars[:1]
        bars[0].left_pct = (segment_days - 0.375) / day_count * 100
        bars[0].width_pct = 0.875 / day_count * 100
    timeline = SimpleNamespace(days=days, bars=bars, total_columns=day_count,
                               lane_count=1, has_overlap=False, has_anomaly=False)
    calendar = env.get_template("components/ui.html").module.room_timeline(
        timeline, start, False, "示例房间近期安排", "0")
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{ADMIN_CSS}</style>'
        '</head><body class="admin-body">'
        '<div class="room-operations-list"><article class="room-operation-card">'
        f'{calendar}</article></div></body></html>'
    )


@pytest.mark.parametrize("width", [390, 1280])
def test_timeline_fits_without_horizontal_scrolling(browser: Browser, width: int) -> None:
    """18 天完整分段显示；日期可读且没有隐藏横向内容。"""
    page = browser.new_page(viewport={"width": width, "height": 900}, java_script_enabled=False)
    # 禁用脚本时通过本地拦截响应装载，不依赖 document.write 注入页面。
    page.route("http://room-preview.test/", lambda route: route.fulfill(
        content_type="text/html", body=_timeline_fixture(18)))
    page.goto("http://room-preview.test/")
    # 页面本身在任何宽度下都不得横向滚动。
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")

    if width < 620:
        # 手机版式（2026-09-16 起）：不分段，18 天全部在一条带上。
        # 天数多到放不下时由 .cal-m__viewport 内部横滚，格子保持 44px 可读，
        # 不像旧版那样把日期压窄。生产窗口只有 6 天，实际不会出现滚动条。
        cells = page.locator(".cal-m__day:visible")
        assert cells.count() == 18
        assert cells.evaluate_all("els => els.every(el => el.clientWidth >= 44)")
        assert page.locator(".cal-m__viewport:visible").evaluate_all(
            "els => els.every(el => el.scrollWidth >= el.clientWidth)")
        # 迷你条与日期条同在滚动容器内，滚动时保持对齐。
        assert page.locator(".cal-m__viewport .cal-m__rail").count() == 1
        page.close()
        return

    cells = page.locator(".cal__date:visible")
    assert cells.count() == 18
    assert page.locator(".cal__scroll:visible").evaluate_all(
        "els => els.every(el => el.scrollWidth <= el.clientWidth)")
    assert cells.evaluate_all(
        "els => els.every(el => el.clientWidth >= 70 && el.clientHeight == 44)")
    segment = page.locator(".cal__segment:visible").first
    assert segment.locator(".cal__bar:visible").count() == 3
    if width == 390:
        assert segment.locator(".cal__identity:visible").count() == 3
    segment.locator("summary").press("Enter")
    assert segment.locator(".cal__bar:visible").count() == 5
    assert segment.locator(".cal__bar:visible").evaluate_all(
        "els => new Set(els.map(el => el.getBoundingClientRect().top)).size == 5")
    assert segment.locator('.cal__bar[href="/employee/customers/5"]').is_visible()
    page.close()


def test_each_date_segment_collapses_to_three_rows(browser: Browser) -> None:
    """真实分段夹具下，各段折叠态同为三行。

    这条测试的判据来回改过两次，值得记一笔：最初断言「各段高度必须一致」，
    后来因手机端空轨道问题改成「每段按自身笔数收紧」，
    2026-09-16 手机独立版式后下限按用户要求恢复，于是又回到同高——
    但机理不同：现在是三行下限托底，不是跨段对齐到最多的那段。
    两者的区别由 test_calendar_uneven_segments_expand_to_their_own_row_counts
    在展开态区分，本测试只守折叠态的稳定高度。
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_timeline_fixture(14, sparse_tail=True))

    tracks = page.locator(".cal__segment:visible .cal__tracks")
    heights = tracks.evaluate_all(
        "els => els.map(el => Math.round(el.getBoundingClientRect().height))"
    )
    counts = page.locator(".cal__segment:visible").evaluate_all(
        """els => els.map(el => [...el.querySelectorAll('.cal__bar')]
             .filter(bar => bar.offsetParent).length)"""
    )
    assert len(set(counts)) > 1, "这个夹具本身要造出各段笔数不同，否则守不住任何东西"

    for index, (height, count) in enumerate(zip(heights, counts, strict=True)):
        assert height == 3 * _CAL_TRACK_HEIGHT_DESKTOP, (
            f"第 {index + 1} 段有 {count} 笔，折叠态应为 3 行，实际 {height}"
        )
    page.close()


def test_a_stay_split_across_segments_says_it_is_the_same_booking(
    browser: Browser,
) -> None:
    """被段边界切开的同一笔订单，两段都必须写明是延续，不能读成两单。

    手机端每段只放 3 天，一笔跨段住宿会在相邻两段各出现一行同名同晚数的条目。
    没有延续标注时，这与「一笔连住被拆成两单」的错误数据长得一模一样——那正是
    上一轮用户报的缺陷，不能靠版面再制造一次同样的观感。
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_timeline_fixture(14, crossing=True))

    # 原先此处还断言 .cal__identity 在两段各出现一行。该元素默认 display:none，
    # 只在旧的手机紧凑布局里显示过；2026-09-16 手机改用 _room_timeline_mobile 后
    # 那条规则随之移除，它在任何视口都不再可见。
    # 跨段延续的守护改由下面的 title 与 .cal__cont 无障碍标签承担，覆盖未减少。
    titles = page.locator(".cal__bar:visible").evaluate_all(
        "els => els.map(el => el.getAttribute('title'))"
    )
    assert "续下段" in titles[0] and "接上段" in titles[1], titles
    labels = page.locator(".cal__cont").evaluate_all(
        "els => els.map(el => el.getAttribute('aria-label'))"
    )
    assert {"同一笔，续下段", "同一笔，接上段"} <= set(labels), labels
    page.close()


def test_the_date_module_is_not_a_dead_keyboard_stop(browser: Browser) -> None:
    """日期段不再横向滚动，就不该占着一个什么都做不了的 Tab 停留点。

    tabindex 原本是为了让键盘用户能滚动看后面的日子；改成纵向分段后所有日期都
    已经在页面上，停在这里既不能滚也不能操作。区域名对读屏仍有用，因此 role 与
    aria-label 保留，只去掉焦点位。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_content(_timeline_fixture(18))

    scroll = page.locator(".cal__scroll").first
    assert scroll.get_attribute("tabindex") is None, "不滚动的容器不应可聚焦"
    assert scroll.get_attribute("role") == "group"
    assert scroll.get_attribute("aria-label"), "区域仍需有名字供读屏定位"
    assert scroll.evaluate("el => el.scrollWidth <= el.clientWidth"), (
        "如果又滚动起来，就必须把 tabindex 加回来"
    )
    page.close()


def test_timeline_segment_clipping_keeps_exact_endpoints(browser: Browser) -> None:
    """换行只裁切展示，两个片段合计仍为 21 小时，客户链接不改变。"""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_timeline_fixture(14, crossing=True))
    pieces = page.locator(".cal__bar:visible")
    assert pieces.count() == 2
    positions = pieces.evaluate_all("""els => els.map(el => ({
        left: parseFloat(el.style.left), width: parseFloat(el.style.width),
        href: el.getAttribute('href')
    }))""")
    # 段宽 7 天 = 168 小时：边界前 9 小时占 5.3571%，边界后 12 小时占 7.1429%。
    assert positions[0]["left"] == pytest.approx(94.6429, abs=0.0001)
    assert positions[0]["width"] == pytest.approx(9 / 168 * 100, abs=0.0001)
    assert positions[1]["left"] == pytest.approx(0)
    assert positions[1]["width"] == pytest.approx(12 / 168 * 100, abs=0.0001)
    assert {p["href"] for p in positions} == {"/employee/customers/1"}
    page.close()


def test_confirm_text_follows_the_action_that_was_clicked(browser: Browser) -> None:
    """确认文案必须跟随点下去的那个动作，而不是表单的默认动作。

    同一个表单挂着归档、分派、取消、永久删除四个按钮，后三个靠 formaction 改写
    目标。表单级 data-confirm 是写给默认动作（归档）的，被它们继承就会出现
    「点分派，问你是否移入归档」——把人引向错误的心理模型，而他正要做的是一件
    完全不同、且会真实改变任务归属的事。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)
    page.check("[data-select-all]")

    page.click('button[formaction$="assign-selected"]')
    assign_prompt = page.evaluate("() => window.confirmCalls.at(-1)")

    assert "分派" in assign_prompt
    assert "归档" not in assign_prompt
    # 占位符换成本次提交的真实数值。
    assert "2 条" in assign_prompt
    assert "阿姨" in assign_prompt
    page.close()


def test_default_action_still_uses_the_form_level_confirm(browser: Browser) -> None:
    """没有改写目标的默认按钮仍然读表单级文案，不能因为收紧规则而丢掉确认。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)
    page.check("[data-select-all]")

    page.click("button[type='submit']:not([formaction]):not([data-typed-confirm])")

    assert "移入归档" in page.evaluate("() => window.confirmCalls.at(-1)")
    page.close()


def test_an_action_without_its_own_wording_asks_nothing_rather_than_the_wrong_thing(
    browser: Browser,
) -> None:
    """改写了目标却没带自己文案的动作，宁可不问，也不问一件它不做的事。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    _load_selection_page(page)
    page.check("[data-select-all]")

    page.click('button[formaction$="other-action"]')

    assert page.evaluate("() => window.confirmCalls") == []
    page.close()


def _countdown_fixture(server_now: str, target: str, kind: str,
                       verified: str = "0", ambiguous: str = "0") -> str:
    """构造带单个倒计时单元的入住安排页面片段。"""
    return f"""<!doctype html>
    <html lang="zh-CN"><head></head><body class="admin-body">
      <main class="page-content">
        <section class="operations-heading" data-operations-refresh
                 data-server-now="{server_now}"></section>
        <span class="countdown" data-target="{target}" data-kind="{kind}"
              data-verified="{verified}" data-ambiguous="{ambiguous}">初始</span>
      </main>
    </body></html>"""


def test_countdown_checkout_shows_hours_and_minutes(browser: Browser) -> None:
    """A04：04:00 观察、12:00 计划退房 → 距计划退房还有 8 小时。"""
    page = browser.new_page()
    page.set_content(_countdown_fixture(
        "2026-09-09T04:00:00+08:00", "2026-09-09T12:00:00+08:00", "checkout"))
    page.add_script_tag(content=ADMIN_SCRIPT)
    text = page.inner_text(".countdown")
    # 8 小时会因加载毫秒流逝 floor 成 7 小时 59 分钟——这是 §6 的 floor 行为；
    # 验证措辞与小时/分钟格式即可，不钉整点。
    assert text.startswith("距计划退房还有")
    assert "小时" in text and "-" not in text
    page.close()


def test_countdown_checkin_shows_distance(browser: Browser) -> None:
    """A04：04:00 观察、15:00 起入住 → 距可入住时间还有 11 小时。"""
    page = browser.new_page()
    page.set_content(_countdown_fixture(
        "2026-09-09T04:00:00+08:00", "2026-09-09T15:00:00+08:00", "checkin"))
    page.add_script_tag(content=ADMIN_SCRIPT)
    text = page.inner_text(".countdown")
    assert text.startswith("距可入住时间还有")
    assert "小时" in text and "-" not in text
    page.close()


def test_countdown_past_checkout_is_waiting_not_negative(browser: Browser) -> None:
    """A05：过了计划退房未核验 → 显示已过时间与退房待确认，不显示负数。"""
    page = browser.new_page()
    page.set_content(_countdown_fixture(
        "2026-09-09T12:30:00+08:00", "2026-09-09T12:00:00+08:00", "checkout"))
    page.add_script_tag(content=ADMIN_SCRIPT)
    text = page.inner_text(".countdown")
    assert "退房待确认" in text
    assert "计划退房时间已过" in text and "分钟" in text
    assert "-" not in text
    page.close()


def test_countdown_past_checkin_is_arrival_pending(browser: Browser) -> None:
    """A06：过了 15:00 无实际到店 → 已到可入住时间 · 到店待确认。"""
    page = browser.new_page()
    page.set_content(_countdown_fixture(
        "2026-09-09T15:30:00+08:00", "2026-09-09T15:00:00+08:00", "checkin"))
    page.add_script_tag(content=ADMIN_SCRIPT)
    assert "已到可入住时间 · 到店待确认" in page.inner_text(".countdown")
    page.close()


def test_countdown_verified_checkout_stops(browser: Browser) -> None:
    """A07：已核验退房 → 显示已退房，不再倒计时。"""
    page = browser.new_page()
    page.set_content(_countdown_fixture(
        "2026-09-09T11:00:00+08:00", "2026-09-09T12:00:00+08:00", "checkout",
        verified="1"))
    page.add_script_tag(content=ADMIN_SCRIPT)
    assert page.inner_text(".countdown").strip() == "已退房"
    page.close()


# --- 日历轨道高度（R-03） ---------------------------------------------------

_CAL_TRACK_HEIGHT_MOBILE = 88
_CAL_TRACK_HEIGHT_DESKTOP = 54


def _render_room_timeline(bar_count: int, *, days: int = 6) -> str:
    """用真实宏渲染一段合成时间轴。

    只造合成姓名与均分的住宿区间，不引入任何真实订单或客户数据。
    走真实模板而不是手写 HTML，才能同时覆盖模板里的 --rows 与 CSS 里的高度规则。
    """
    from datetime import date, timedelta

    from jinja2 import Environment, FileSystemLoader

    from homestay_bot.services.admin_operations_service import TimelineBar

    start = date(2026, 9, 14)
    day_items = [
        # occupied 必须给：模板用它把无人住的日期压灰，缺失时 Jinja 取到
        # undefined（假值），会让每一天都显示成空房，测试便测不出这条规则。
        SimpleNamespace(local_date=start + timedelta(days=offset), occupied=bar_count > 0)
        for offset in range(days)
    ]
    # 让每条住宿都横跨整个窗口，从而必定落在同一分段里，
    # 使可见条数等于 bar_count，轨道数只由 --rows 规则决定。
    bars = [
        TimelineBar(
            order_id=index + 1,
            customer_id=index + 1,
            guest_name=f"测试客人{index + 1}",
            nights=2,
            left_pct=0.0,
            width_pct=100.0,
            lane=index,
            left_continues=False,
            right_continues=False,
            checkout_verified=False,
            start_label="今天 15:00",
            end_label="明天 12:00",
        )
        for index in range(bar_count)
    ]
    timeline = SimpleNamespace(
        days=tuple(day_items),
        bars=tuple(bars),
        has_overlap=False,
        has_anomaly=False,
    )

    env = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "src/homestay_bot/templates"),
        autoescape=True,
    )
    template = env.from_string(
        "{% import 'components/ui.html' as ui %}"
        "{{ ui.room_timeline(timeline, today, false, '测试房间') }}"
    )
    # 宽/紧凑两套布局由 @container (max-width: 620px) 切换，容器是
    # .room-operation-card（container-type: inline-size）。不套这层的话
    # 容器查询永远不命中，紧凑布局会一直 display:none，量到的高度恒为 0。
    return (
        '<article class="room-operation-card">'
        + template.render(timeline=timeline, today=start)
        + "</article>"
    )


def _tracks_height(page: Page, *, compact: bool) -> float:
    """返回当前生效布局里第一个轨道容器的高度。"""
    layout = ".cal__layout--compact" if compact else ".cal__layout--wide"
    height = page.evaluate(
        "(sel) => {"
        "  const el = document.querySelector(sel + ' .cal__tracks');"
        "  return el ? el.getBoundingClientRect().height : -1;"
        "}",
        layout,
    )
    return float(height)


@pytest.mark.parametrize("bar_count", [1, 2])
def test_calendar_collapsed_height_holds_three_tracks(browser: Browser, bar_count: int) -> None:
    """桌面折叠态恒为三条轨道，使各房间卡高度一致。

    这个下限一度被去掉：当时手机端与桌面共用同一套甘特图，切成 3 天一段后
    一笔订单会撑出三条空轨道。2026-09-16 手机改用独立版式后，那个理由不再成立，
    下限按用户要求恢复——它换来的是卡片高度稳定与重叠订单的落脚空间。
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_render_room_timeline(bar_count))
    page.add_style_tag(content=ADMIN_CSS)

    height = _tracks_height(page, compact=False)
    assert height == pytest.approx(3 * _CAL_TRACK_HEIGHT_DESKTOP), (
        f"{bar_count} 条订单折叠态应占 3 条轨道，实际高度 {height}"
    )
    page.close()


def test_calendar_tracks_cap_preview_at_three(browser: Browser) -> None:
    """超过三条时折叠态封顶三条轨道，展开后按真实行数铺开。"""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_render_room_timeline(6))
    page.add_style_tag(content=ADMIN_CSS)

    collapsed = _tracks_height(page, compact=False)
    assert collapsed == pytest.approx(3 * _CAL_TRACK_HEIGHT_DESKTOP), (
        f"折叠态应封顶三条轨道，实际 {collapsed}"
    )

    page.evaluate(
        "() => document.querySelector('.cal__layout--wide .cal__more').open = true"
    )
    expanded = _tracks_height(page, compact=False)
    assert expanded == pytest.approx(6 * _CAL_TRACK_HEIGHT_DESKTOP), (
        f"展开后应铺开全部六条轨道，实际 {expanded}"
    )
    page.close()


def test_calendar_empty_keeps_message_visible(browser: Browser) -> None:
    """没有订单时仍要留出高度显示空状态文案，不能塌成零高。"""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_render_room_timeline(0))
    page.add_style_tag(content=ADMIN_CSS)

    height = _tracks_height(page, compact=False)
    assert height > 0, "空日历塌成零高会让空状态文案不可见"
    assert page.locator(".cal__layout--wide .cal__empty").first.is_visible()
    page.close()


def _render_uneven_timeline(counts: list[int], *, segment_size: int = 7) -> str:
    """渲染各分段订单数不同的时间轴。

    既有 fixture 让每条住宿横跨整个窗口，于是每段可见数都相同——
    这种形状下「各段取真实行数」与「各段统一取最大行数」渲染结果完全一致，
    测不出两者的区别。这里按段放置订单，让分段之间行数不等。
    """
    from datetime import date, timedelta

    from jinja2 import Environment, FileSystemLoader

    from homestay_bot.services.admin_operations_service import TimelineBar

    start = date(2026, 9, 14)
    days = len(counts) * segment_size
    day_items = [
        SimpleNamespace(local_date=start + timedelta(days=offset)) for offset in range(days)
    ]

    span = 100.0 / len(counts)
    bars = []
    order = 0
    for seg_index, count in enumerate(counts):
        for _ in range(count):
            order += 1
            bars.append(
                TimelineBar(
                    order_id=order,
                    customer_id=order,
                    guest_name=f"测试客人{order}",
                    nights=1,
                    # 留出边距，避免浮点误差让订单落进相邻分段。
                    left_pct=seg_index * span + span * 0.1,
                    width_pct=span * 0.8,
                    lane=0,
                    left_continues=False,
                    right_continues=False,
                    checkout_verified=False,
                )
            )

    timeline = SimpleNamespace(
        days=tuple(day_items),
        bars=tuple(bars),
        has_overlap=False,
        has_anomaly=False,
    )
    env = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "src/homestay_bot/templates"),
        autoescape=True,
    )
    template = env.from_string(
        "{% import 'components/ui.html' as ui %}"
        "{{ ui.room_timeline(timeline, today, false, '测试房间') }}"
    )
    return (
        '<article class="room-operation-card">'
        + template.render(timeline=timeline, today=start)
        + "</article>"
    )


def _segment_heights(page: Page, *, compact: bool) -> list[float]:
    """返回当前布局里每个分段轨道容器的高度。"""
    layout = ".cal__layout--compact" if compact else ".cal__layout--wide"
    return page.evaluate(
        "(sel) => Array.from(document.querySelectorAll(sel + ' .cal__tracks'))"
        "  .map(el => el.getBoundingClientRect().height)",
        layout,
    )


@pytest.mark.parametrize("counts", [[1, 3], [1, 4, 0]])
def test_calendar_uneven_segments_collapse_to_the_same_three_rows(
    browser: Browser, counts: list[int]
) -> None:
    """折叠态各段都是三行——这是下限带来的，不是跨段对齐。

    两者形状相同但机理不同，下一条测试用展开态把它们区分开。
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_render_uneven_timeline(counts))
    page.add_style_tag(content=ADMIN_CSS)

    heights = _segment_heights(page, compact=False)
    assert len(heights) == len(counts), f"应渲染 {len(counts)} 段，实际 {len(heights)}"
    for index, height in enumerate(heights):
        assert height == pytest.approx(3 * _CAL_TRACK_HEIGHT_DESKTOP), (
            f"第 {index + 1} 段折叠态应为 3 行，实际 {height}"
        )
    page.close()


def test_calendar_uneven_segments_expand_to_their_own_row_counts(
    browser: Browser,
) -> None:
    """展开后各段按自己的笔数铺开，不被最多的那段拉齐。

    这才是「不跨段对齐」的真正判据：若实现改回取全局最大笔数，
    只有一笔的那段展开后会变成四行，本测试立刻失败。
    折叠态恒三行是下限的结果，区分不出两种实现。
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_render_uneven_timeline([1, 4]))
    page.add_style_tag(content=ADMIN_CSS)

    page.evaluate(
        "() => document.querySelectorAll('.cal__layout--wide .cal__more')"
        "  .forEach(el => { el.open = true; })"
    )
    heights = _segment_heights(page, compact=False)
    # 一笔的段受三行下限托底；四笔的段按真实行数铺开。
    assert heights[0] == pytest.approx(3 * _CAL_TRACK_HEIGHT_DESKTOP), (
        f"只有一笔的段展开后应为三行下限，实际 {heights[0]}"
    )
    assert heights[1] == pytest.approx(4 * _CAL_TRACK_HEIGHT_DESKTOP), (
        f"四笔的段展开后应铺开四条轨道，实际 {heights[1]}"
    )
    page.close()


def test_calendar_desktop_collapsed_height_is_stable(browser: Browser) -> None:
    """桌面宽屏折叠态同样恒为三行，房间卡之间不会高低不齐。"""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_content(_render_room_timeline(2))
    page.add_style_tag(content=ADMIN_CSS)

    height = _tracks_height(page, compact=False)
    assert height == pytest.approx(3 * _CAL_TRACK_HEIGHT_DESKTOP), (
        f"桌面两条订单应占两条轨道，实际 {height}"
    )
    page.close()


# --- 顶栏不透字（R-02） -----------------------------------------------------


def test_topbar_background_is_opaque(browser: Browser) -> None:
    """顶栏背景必须完全不透明，否则正文滚到它下面会透出来。

    真机实测顶栏为 rgba(255,255,255,0.82) 且带 backdrop-filter，
    滚动时卡片文字从标题栏里显出来。这里断言计算样式的 alpha，
    而不是比对截图像素——像素阈值会随字体渲染和设备变化。
    """
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _load_admin_page(page)

    alpha = page.evaluate(
        "() => {"
        "  const cs = getComputedStyle(document.querySelector('.topbar'));"
        "  const m = cs.backgroundColor.match(/rgba?\\(([^)]+)\\)/);"
        "  const parts = m[1].split(',').map(s => parseFloat(s));"
        "  return parts.length > 3 ? parts[3] : 1;"
        "}"
    )
    assert alpha == 1, f"顶栏背景透明度为 {alpha}，正文会透出来"
    page.close()


def test_topbar_keeps_layering_below_drawer(browser: Browser) -> None:
    """顶栏改成实色后，抽屉与遮罩仍必须盖在它上面。

    Spec 要求最小修复，不许为了盖住正文而全站抬高 z-index。
    """
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _load_admin_page(page)

    layers = page.evaluate(
        "() => ['.topbar', '.drawer-backdrop', '.admin-sidebar'].map("
        "  sel => parseInt(getComputedStyle(document.querySelector(sel)).zIndex, 10)"
        ")"
    )
    topbar, backdrop, drawer = layers
    assert topbar < backdrop < drawer, (
        f"层级被破坏：顶栏 {topbar} / 遮罩 {backdrop} / 抽屉 {drawer}"
    )
    page.close()


# --- 手机版式（Spec 2026-09-16） -------------------------------------------


def _mobile_page(browser: Browser, html: str, *, width: int = 360) -> Page:
    """在手机宽度下装载房间卡，并注入真实 CSS。"""
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.set_content(html)
    page.add_style_tag(content=ADMIN_CSS)
    return page


@pytest.mark.parametrize("width", [360, 390])
def test_mobile_timeline_has_no_page_level_horizontal_overflow(
    browser: Browser, width: int
) -> None:
    """M01：生产窗口（6 天）下页面与卡片都不出现横向滚动。"""
    page = _mobile_page(browser, _render_room_timeline(2), width=width)
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    # 6 天 × 48px 放得下，滚动容器不应真的产生滚动。
    assert page.evaluate(
        "() => [...document.querySelectorAll('.cal-m__viewport')]"
        "  .every(el => el.scrollWidth <= el.clientWidth)"
    )
    page.close()


def test_mobile_timeline_stays_within_a_height_budget(browser: Browser) -> None:
    """M02：手机版式的高度必须控制在预算内。

    不拿桌面渲染当基线——桌面横向空间充裕，单段甘特图本就更矮，
    比它只会得出「手机更高」这种没有意义的结论。旧手机版式已被本次改动删除，
    也无法再实时对照。因此改用绝对预算：表头与日期条固定开销 + 每笔订单一张卡。

    旧手机版式在同样两笔订单下占 489px（2 段 × 3 条 88px 轨道加日期头），
    预算 260px 因此仍有近一倍的余量，但足以拦住「又退回按轨道铺开」的回归。
    """
    for count, budget in [(1, 200), (2, 260), (4, 420)]:
        page = _mobile_page(browser, _render_room_timeline(count))
        height = page.evaluate(
            "() => document.querySelector('.cal-m').getBoundingClientRect().height"
        )
        page.close()
        assert height <= budget, f"{count} 笔订单占 {height}px，超出预算 {budget}px"


def test_mobile_stay_card_is_a_full_size_touch_target(browser: Browser) -> None:
    """M03：整张订单卡是点击区且不小于 44px，键盘可聚焦。

    只让姓名可点会退化到约 24px，低于触控下限；这是改版时真实踩过的退化。
    """
    page = _mobile_page(browser, _render_room_timeline(2))
    boxes = page.locator(".cal-m__hit").evaluate_all(
        "els => els.map(el => el.getBoundingClientRect().height)"
    )
    assert boxes and min(boxes) >= 44, f"点击区高度 {boxes}"

    assert page.locator("a.cal-m__hit").count() == 2, "有客户号时整卡应是链接"
    focused = page.evaluate(
        "() => { const a = document.querySelector('a.cal-m__hit');"
        "        a.focus(); return document.activeElement === a; }"
    )
    assert focused, "订单卡必须能获得键盘焦点"
    page.close()


def test_mobile_timeline_keeps_every_piece_of_information(browser: Browser) -> None:
    """M04：客户链接、起止、晚数、状态一个不少。"""
    page = _mobile_page(browser, _render_room_timeline(2))
    first = page.locator(".cal-m__item").first
    assert first.locator("a.cal-m__hit").get_attribute("href") == "/employee/customers/1"
    text = first.inner_text()
    assert "测试客人1" in text
    assert "2 晚" in text
    # 断言真实起止文案：只查 "→" 的话，两侧都是空字符串也会通过。
    assert "今天 15:00 → 明天 12:00" in text, f"起止文案未渲染：{text!r}"
    assert first.locator(".cal-m__state").count() == 1
    page.close()


def test_mobile_timeline_empty_window_keeps_the_existing_message(
    browser: Browser,
) -> None:
    """M04：空窗口沿用既有空状态文案，不新造措辞。"""
    page = _mobile_page(browser, _render_room_timeline(0))
    assert page.locator(".cal-m .cal__empty").first.is_visible()
    assert page.locator(".cal-m__item").count() == 0
    page.close()


def test_mobile_timeline_survives_enlarged_system_font(browser: Browser) -> None:
    """M05：字体放大后仍不产生页面级横向溢出。"""
    page = _mobile_page(browser, _render_room_timeline(2))
    page.add_style_tag(content="html { font-size: 22px; }")
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    page.close()


def test_mobile_timeline_does_not_depend_on_has_selector(browser: Browser) -> None:
    """M07：新版不依赖 :has() 折叠，因此没有「展开」这一步。

    旧版靠 `:has(> .cal__more[open])` 控制轨道高度，不支持 :has() 的浏览器
    会看到不完整的数据。新版把全部订单直接列出，天然没有这个降级路径。
    """
    page = _mobile_page(browser, _render_room_timeline(6))
    assert page.locator(".cal-m__item").count() == 6, "六笔订单应全部直接可见"
    assert page.locator(".cal-m .cal__more").count() == 0, "手机版式不应再有展开控件"
    page.close()


def test_mobile_timeline_does_not_stretch_on_mid_width_cards(browser: Browser) -> None:
    """中屏（卡宽 460–620px）下手机版式封顶，不随卡片拉伸。

    这一段既进不了桌面甘特图（短住宿的条内标签放不下），
    铺满又会让日期格被拉到约 90px、订单卡姓名与徽章之间空出一大片。
    """
    page = browser.new_page(viewport={"width": 620, "height": 900})
    page.set_content(_render_room_timeline(2))
    page.add_style_tag(content=ADMIN_CSS)

    geom = page.evaluate(
        "() => {"
        "  const card = document.querySelector('.room-operation-card');"
        "  const m = document.querySelector('.cal-m');"
        "  const strip = document.querySelector('.cal-m__strip');"
        "  const rail = document.querySelector('.cal-m__rail');"
        "  return {card: card.getBoundingClientRect().width,"
        "          cal: m.getBoundingClientRect().width,"
        "          strip: strip.getBoundingClientRect().width,"
        "          rail: rail.getBoundingClientRect().width};"
        "}"
    )
    assert geom["card"] > 460, f"夹具必须落在中屏区间，实际卡宽 {geom['card']}"
    assert geom["cal"] <= 420, f"中屏下日历应封顶 420px，实际 {geom['cal']}"
    # 日期条与迷你条同在 .cal-m 内一起收窄，对齐关系不能因封顶而错位。
    assert geom["strip"] == pytest.approx(geom["rail"], abs=1), (
        f"日期条 {geom['strip']} 与迷你条 {geom['rail']} 宽度不一致，百分比定位会错位"
    )
    page.close()


@pytest.mark.parametrize("width", [360, 390])
def test_mobile_timeline_cap_does_not_reach_phone_widths(
    browser: Browser, width: int
) -> None:
    """手机宽度下不得命中中屏封顶。

    直接断言计算样式里的 max-width 是 none，而不是比对日历宽度——
    手机卡片内容宽本来就不到 420px，比宽度的话封顶生不生效都能通过，
    等于什么都没验证。
    """
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.set_content(_render_room_timeline(2))
    page.add_style_tag(content=ADMIN_CSS)

    cap = page.evaluate(
        "() => getComputedStyle(document.querySelector('.cal-m')).maxWidth"
    )
    assert cap == "none", f"手机宽度 {width} 不应命中中屏封顶，实际 max-width={cap}"
    page.close()
