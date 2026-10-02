"""核对正式应用异常注册，不启动外部服务或应用生命周期。"""

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from homestay_bot.main import app


@pytest.mark.parametrize(
    ("path", "method", "accept", "html"),
    [
        ("/employee/unavailable-test", "GET", "text/html", True),
        ("/employee/unavailable-test", "GET", "text/html;q=0.8,application/json", True),
        ("/employee/unavailable-test", "GET", "text/html;q=0", False),
        ("/employee/unavailable-test", "GET", "application/json", False),
        ("/employee/unavailable-test", "GET", "*/*", False),
        ("/employee/unavailable-test", "POST", "text/html", False),
        ("/employee/health", "GET", "text/html", False),
        ("/outside-unavailable-test", "GET", "text/html", False),
    ],
)
def test_actual_app_503_handler_preserves_contract(monkeypatch, path, method, accept, html):
    """503 只转换后台 HTML GET，原响应头和后台禁止缓存头仍保留。"""
    monkeypatch.setattr(app.router, "routes", list(app.router.routes))

    async def unavailable():
        """合成异常细节不能进入 HTML。"""
        raise HTTPException(503, "synthetic-sensitive-error", headers={"Retry-After": "30"})

    app.router.routes.insert(0, APIRoute(
        path, unavailable, methods=[method],
    ))
    client = TestClient(app)
    response = client.request(method, path, headers={"Accept": accept})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    if path.startswith("/employee/"):
        assert "no-store" in response.headers["cache-control"]
    if html:
        assert "text/html" in response.headers["content-type"]
        assert "页面暂时不可用" in response.text
        assert "synthetic-sensitive-error" not in response.text
        assert 'href="/employee/admin"' in response.text
    else:
        assert response.json() == {"detail": "synthetic-sensitive-error"}


def test_existing_html_response_and_other_http_errors_remain(monkeypatch):
    """已渲染 HTML 不经过异常转换，404 仍采用框架响应。"""
    monkeypatch.setattr(app.router, "routes", list(app.router.routes))

    async def existing():
        """模拟既有诊断 HTML 503。"""
        return HTMLResponse("existing-html", status_code=503)

    async def missing():
        """非 503 不改变契约。"""
        raise HTTPException(404, "synthetic-not-found")

    app.add_api_route("/employee/existing-html-test", existing)
    app.add_api_route("/employee/missing-test", missing)
    client = TestClient(app)
    assert client.get("/employee/existing-html-test").text == "existing-html"
    response = client.get("/employee/missing-test", headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert response.json() == {"detail": "synthetic-not-found"}
