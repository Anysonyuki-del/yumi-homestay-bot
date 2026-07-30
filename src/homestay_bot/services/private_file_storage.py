import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class InvalidPrivateFile(ValueError):
    """表示上传内容、大小或私有文件编号不安全。"""


@dataclass(frozen=True)
class StoredPrivateFile:
    """描述一个仅供服务端授权读取的私有图片。"""

    file_id: str
    path: Path
    content_type: str
    size: int


class PrivateFileStorage:
    """把经过最小真实格式校验的图片保存到私有目录。"""

    _file_id_pattern = re.compile(
        r"^[0-9a-f]{32}\.(?:png|jpg|webp)$"
    )
    _content_types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "webp": "image/webp",
    }

    def __init__(self, root: Path) -> None:
        """创建权限收紧的私有文件根目录。"""
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        self._root = root.resolve()

    async def save_image(
        self,
        stream: BinaryIO,
        content_type: str,
        size_limit: int,
    ) -> StoredPrivateFile:
        """限制大小、核对 MIME 与真实签名后随机命名保存。"""
        if size_limit <= 0:
            raise ValueError("图片大小上限必须大于零")
        content = stream.read(size_limit + 1)
        if len(content) > size_limit:
            raise InvalidPrivateFile("图片大小超过限制")
        if not content:
            raise InvalidPrivateFile("图片内容为空")
        extension = self._detect_extension(content)
        expected_content_type = self._content_types[extension]
        if content_type.lower() != expected_content_type:
            raise InvalidPrivateFile("图片 MIME 与真实格式不一致")

        file_id = f"{uuid4().hex}.{extension}"
        path = self._safe_path(file_id)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return StoredPrivateFile(
            file_id=file_id,
            path=path,
            content_type=expected_content_type,
            size=len(content),
        )

    def open_for_read(self, file_id: str) -> StoredPrivateFile:
        """解析安全文件编号并返回已存在的私有图片。"""
        path = self._safe_path(file_id)
        if not path.is_file():
            raise LookupError("私有文件不存在")
        extension = file_id.rsplit(".", 1)[1]
        return StoredPrivateFile(
            file_id=file_id,
            path=path,
            content_type=self._content_types[extension],
            size=path.stat().st_size,
        )

    def delete(self, file_id: str) -> None:
        """只删除根目录内满足随机编号格式的文件。"""
        self._safe_path(file_id).unlink(missing_ok=True)

    def _safe_path(self, file_id: str) -> Path:
        """拒绝路径分隔符、绝对路径和非随机文件编号。"""
        if self._file_id_pattern.fullmatch(file_id) is None:
            raise InvalidPrivateFile("私有文件编号无效")
        path = (self._root / file_id).resolve()
        if path.parent != self._root:
            raise InvalidPrivateFile("私有文件路径越界")
        return path

    @staticmethod
    def _detect_extension(content: bytes) -> str:
        """用文件首尾结构识别 PNG、JPEG 或 WebP。"""
        if (
            content.startswith(b"\x89PNG\r\n\x1a\n")
            and b"IHDR" in content[:32]
            and content.endswith(b"IEND\xaeB`\x82")
        ):
            return "png"
        if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
            return "jpg"
        if (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        ):
            return "webp"
        raise InvalidPrivateFile("图片真实格式不受支持")
