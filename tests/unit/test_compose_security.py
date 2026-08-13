from pathlib import Path


def test_api_port_is_only_published_on_loopback() -> None:
    """生产入口必须经由 Nginx，禁止 Docker 直接公开 HTTP 端口。"""
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8000:8000"' in compose
    assert '\n      - "8000:8000"' not in compose
