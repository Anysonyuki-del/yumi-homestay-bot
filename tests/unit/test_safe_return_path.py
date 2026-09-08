"""校验回跳路径的边界；这是唯一挡住开放重定向的地方。"""

from homestay_bot.routes.page_errors import safe_return_path


def test_rejects_off_site_targets() -> None:
    """带 scheme 或 netloc 的目标一律回落，避免开放重定向。"""
    for hostile in (
        "https://evil.example.com/steal",
        "//evil.example.com/steal",
        "http://127.0.0.1/admin",
        "/admin/secret",
        "",
        None,
    ):
        assert safe_return_path(hostile) == "/employee/tasks"


def test_keeps_query_string() -> None:
    """筛选与页码在查询串里，丢了就等于没回跳。"""
    assert (
        safe_return_path("/employee/tasks?status_filter=pending&page=2")
        == "/employee/tasks?status_filter=pending&page=2"
    )


def test_keeps_the_fragment_so_long_lists_return_to_the_right_row() -> None:
    """锚点必须保留：运营页有几十间房，回到页首等于还要再找一遍。

    锚点是纯客户端的，浏览器不会把它发给服务器；scheme 与 netloc 已在上面被
    拒，因此保留锚点不会打开重定向缺口。
    """
    assert (
        safe_return_path("/employee/admin/operations?days=7#room-101")
        == "/employee/admin/operations?days=7#room-101"
    )
    assert (
        safe_return_path("/employee/admin/operations#room-101")
        == "/employee/admin/operations#room-101"
    )
    # 锚点里塞站外地址也无害：Location 的路径部分仍是本站。
    assert safe_return_path("/employee/tasks#//evil.example.com").startswith(
        "/employee/tasks#"
    )


def test_custom_fallback_is_used_when_the_candidate_is_unusable() -> None:
    """不同页面的兜底不该都掉进任务中心。"""
    assert (
        safe_return_path("https://evil.example.com", fallback="/employee/properties/9")
        == "/employee/properties/9"
    )
    assert (
        safe_return_path(None, fallback="/employee/properties/9")
        == "/employee/properties/9"
    )
