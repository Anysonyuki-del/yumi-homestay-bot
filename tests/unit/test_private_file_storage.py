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
