from pathlib import Path


def test_api_port_is_only_published_on_loopback() -> None:
    """生产入口必须经由 Nginx，禁止 Docker 直接公开 HTTP 端口。"""
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8000:8000"' in compose
    assert '\n      - "8000:8000"' not in compose


def test_private_uploads_use_one_persistent_container_path() -> None:
    """API 私有上传必须落到可配置宿主机目录，不能留在容器可写层。"""
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "PRIVATE_UPLOAD_DIR: /app/data/private_uploads" in compose
    assert (
        '"${PRIVATE_UPLOAD_HOST_DIR:-./data/private_uploads}:'
        '/app/data/private_uploads"'
    ) in compose
