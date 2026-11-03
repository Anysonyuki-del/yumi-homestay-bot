"""后台「小程序首页」页面（三标签 Spec F4/F6）：真实路由与服务渲染，浏览器核对手机宽度与删除确认。

保存、上传、排序、删除的服务端行为由 tests/integration/test_miniprogram_home.py 覆盖；
这里只看页面本身：公开提示、横幅删除按钮关联到带确认提示的独立表单、手机宽度不横向滚动。
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.sync_api import Browser

from homestay_bot.domain.enums import EmployeeRole
from tests.browser.test_admin_interactions import (
    ADMIN_CSS,
    ADMIN_SCRIPT,
    browser,  # noqa: F401
    playwright_runtime,  # noqa: F401
)
from tests.integration.test_miniprogram_home import PAGE, STORE, _app
from tests.integration.test_room_showcase import _png


def _admin_page_with_banner(tmp_path: Path) -> str:
    """经真实路由保存门店信息、上传一张横幅，返回后台页面 HTML。"""
    client, _, _ = _app(tmp_path, EmployeeRole.ADMIN)

    def token() -> str:
        return client.get(PAGE).text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    assert client.post(f"{PAGE}/store", data={"csrf_token": token(), **STORE}).status_code == 200
    client.post(
        f"{PAGE}/banners",
        data={"csrf_token": token()},
        files={"image": ("banner.png", _png(), "image/png")},
    )
    response = client.get(PAGE)
    assert response.status_code == 200
    return response.text


def test_admin_page_on_phone_links_delete_to_confirmed_form(
    tmp_path: Path,
    browser: Browser,  # noqa: F811
) -> None:
    """有一张横幅时：公开提示可见、删除按钮提交到带确认的独立表单、取消确认不提交、手机不横向滚动。"""
    # Playwright 同步接口占用了当前线程的事件循环，建库与请求放到独立线程里做。
    with ThreadPoolExecutor(max_workers=1) as worker:
        html = worker.submit(_admin_page_with_banner, tmp_path).result()

    page = browser.new_page(viewport={"width": 390, "height": 844})
    submitted: list[str] = []
    page.route("**/*", lambda route: route.abort())
    try:
        page.set_content(html)
        page.add_style_tag(content=ADMIN_CSS)
        page.add_script_tag(content=ADMIN_SCRIPT)
        page.on("request", lambda request: submitted.append(request.method))
        body = page.locator("body").inner_text()
        assert "以下内容会公开显示在小程序里" in body
        assert page.locator('input[name="name"]').input_value() == "山野小住"
        assert page.locator('input[name="latitude"]').input_value() == "30.25"

        delete = page.get_by_role("button", name="删除")
        form_id = delete.get_attribute("form")
        assert form_id is not None
        form = page.locator(f"form#{form_id}")
        assert form.get_attribute("action", timeout=1000).endswith("/delete")
        assert form.get_attribute("data-confirm") == "确定删除这张横幅吗？"

        # 管理员在确认框点取消：不发出删除请求。
        page.once("dialog", lambda dialog: dialog.dismiss())
        delete.click()
        assert "POST" not in submitted
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        page.close()
