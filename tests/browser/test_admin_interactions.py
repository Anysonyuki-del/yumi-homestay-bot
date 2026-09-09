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


def _timeline_fixture(day_count: int) -> str:
    """返回两张并排房间卡片，各带一条 day_count 天的日期日历模块。"""
    dates = "".join(
        '<div class="cal__date"><span class="cal__dow">周一</span>'
        f'<span class="cal__dnum">8/{index + 1}</span></div>'
        for index in range(day_count)
    )
    bar = (
        '<a class="cal__bar cal__bar--future" style="left: 0%; width: 14%; top: 8px;" '
        'href="/employee/customers/1"><span class="cal__bar-label">客人示例 · 1 晚'
        "</span></a>"
    )
    card = (
        '<article class="room-operation-card"><div class="cal">'
        f'<div class="cal__scroll" tabindex="0" role="group" aria-label="近期安排">'
        f'<div class="cal__content" style="--days: {day_count};">'
        f'<div class="cal__dates">{dates}</div>'
        f'<div class="cal__tracks" style="height: 60px;">{bar}</div>'
        "</div></div></div></article>"
    )
    return f"""<!doctype html>
    <html lang="zh-CN"><head></head><body class="admin-body">
      <main class="page-content">
        <div class="room-operations-list">{card}{card}</div>
      </main>
    </body></html>"""


def test_timeline_day_cells_stay_wide_enough_to_read(browser: Browser) -> None:
    """半宽卡片里的 14 天时间轴不得把日期格压到标签放不下。

    这条量的是真实渲染宽度：`minmax(0, 1fr)` 会让 18 天等分半宽卡片，每格只剩
    二十几像素，而「退房 1」本身就要四十多像素，相邻信息直接叠在一起。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_content(_timeline_fixture(18))
    page.add_style_tag(content=ADMIN_CSS)

    narrowest = page.evaluate(
        """() => Math.min(...Array.from(document.querySelectorAll(".cal__date"))
             .map((cell) => cell.getBoundingClientRect().width))"""
    )

    # 日期列至少 104px（与 minmax 下限一致），装不下时应横向滚动而非继续压缩。
    assert narrowest >= 104
    page.close()


def test_a_timeline_too_long_for_its_card_scrolls_instead_of_shrinking(
    browser: Browser,
) -> None:
    """装不下时时间轴自己横向滚动，而不是把每一天压得更窄。

    只看 `scrollWidth > clientWidth` 不够：日期格被压窄时，超出的标签同样会把
    滚动宽度撑大。这里要求滚动宽度真的等于「每格都拿到最小宽度」之后的总和。
    """
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_content(_timeline_fixture(18))
    page.add_style_tag(content=ADMIN_CSS)

    measured = page.evaluate(
        """() => {
             const list = document.querySelector(".cal__scroll");
             return {scroll: list.scrollWidth, visible: list.clientWidth};
           }"""
    )

    assert measured["scroll"] > measured["visible"]
    assert measured["scroll"] >= 18 * 104
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
