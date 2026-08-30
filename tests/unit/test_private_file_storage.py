import os
from io import BytesIO

import pytest

from homestay_bot.services.private_file_storage import (
    InvalidPrivateFile,
    PrivateFileStorage,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_valid_image_is_saved_with_random_private_name(tmp_path) -> None:
    """合法图片应使用随机文件编号保存，不能沿用上传文件名。"""
    storage = PrivateFileStorage(tmp_path)

    stored = await storage.save_image(
        BytesIO(PNG_BYTES),
        content_type="image/png",
        size_limit=1024,
    )

    assert stored.file_id.endswith(".png")
    assert "guest-upload" not in stored.file_id
    assert stored.path.parent == tmp_path.resolve()
    assert stored.path.read_bytes() == PNG_BYTES
    assert stored.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_oversized_image_is_rejected_without_file(tmp_path) -> None:
    """超过上限的图片不得留下部分文件。"""
    storage = PrivateFileStorage(tmp_path)

    with pytest.raises(InvalidPrivateFile, match="大小"):
        await storage.save_image(
            BytesIO(PNG_BYTES + b"x" * 100),
            content_type="image/png",
            size_limit=len(PNG_BYTES),
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_fake_image_content_is_rejected(tmp_path) -> None:
    """仅伪造图片 MIME 的文本不得写入私有目录。"""
    storage = PrivateFileStorage(tmp_path)

    with pytest.raises(InvalidPrivateFile, match="格式"):
        await storage.save_image(
            BytesIO(b"this is not an image"),
            content_type="image/png",
            size_limit=1024,
        )

    assert list(tmp_path.iterdir()) == []


def test_private_file_id_rejects_path_traversal(tmp_path) -> None:
    """读取接口必须拒绝目录穿越和任意文件名。"""
    storage = PrivateFileStorage(tmp_path)

    with pytest.raises(InvalidPrivateFile):
        storage.open_for_read("../secret.env")
    with pytest.raises(InvalidPrivateFile):
        storage.open_for_read("/etc/passwd")


def test_private_storage_write_probe_leaves_no_file(tmp_path) -> None:
    """启动写入探针成功后不得在私有目录留下临时文件。"""
    storage = PrivateFileStorage(tmp_path)

    storage.verify_writable()

    assert list(tmp_path.iterdir()) == []


def test_private_storage_write_probe_propagates_permission_failure(
    tmp_path,
    monkeypatch,
) -> None:
    """挂载目录不可写时必须让应用启动失败，不能静默降级。"""
    storage = PrivateFileStorage(tmp_path)

    def reject_write(*args, **kwargs):
        """模拟生产挂载目录拒绝创建文件。"""
        del args, kwargs
        raise PermissionError("private upload mount is read-only")

    monkeypatch.setattr(os, "open", reject_write)

    with pytest.raises(PermissionError, match="read-only"):
        storage.verify_writable()

    assert list(tmp_path.iterdir()) == []
