"""公开照片上传前去除元数据（小程序住客自助 Spec F4）。

手机拍摄的 JPEG 常带 EXIF GPS 坐标，房间照片会公开给任何人，所以必须去掉。
只按段（JPEG）或块（PNG）删除元数据，不解码、不重编码图像，像素字节原样保留；
结构不完整的文件一律拒绝，不原样保存。

ponytail: 只剥离元数据，不压缩、不缩放；需要缩略图或压缩时再评估引入图像库。
"""

import io
import struct
from typing import BinaryIO

from homestay_bot.services.private_file_storage import (
    InvalidPrivateFile,
    PrivateFileStorage,
    StoredPrivateFile,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# 文本、EXIF 与时间块都可能含拍摄设备、地点或作者信息；其余块与图像显示有关，保留。
_PNG_DROPPED_CHUNKS = frozenset({b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"})
# JPEG 中没有长度字段的独立标记：TEM 与 RST0–RST7。
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_JPEG_SOS = 0xDA
_JPEG_APP2 = 0xE2
_JPEG_COM = 0xFE


# 公开图片只收 JPG、PNG，单张不超过 5 MB（用户 2026-11-03 由 2 MB 放宽）；与知识图片同一口径。
PUBLIC_IMAGE_TYPES = frozenset({"image/jpeg", "image/png"})
PUBLIC_IMAGE_MAX_BYTES = 5 * 1024 * 1024


async def save_public_image(
    storage: PrivateFileStorage, stream: BinaryIO, content_type: str, upload_limit: int
) -> StoredPrivateFile:
    """校验类型与大小、去掉元数据后落盘，房间照片与首页横幅共用。

    只负责写文件；调用方登记数据库记录失败时须自行删除返回的文件，避免留下孤儿。
    """
    normalized_type = content_type.lower()
    if normalized_type not in PUBLIC_IMAGE_TYPES:
        raise ValueError("只支持 JPG 或 PNG 图片")
    size_limit = min(upload_limit, PUBLIC_IMAGE_MAX_BYTES)
    content = stream.read(size_limit + 1)
    if len(content) > size_limit:
        raise ValueError("图片大小超过限制")
    cleaned = strip_image_metadata(content, normalized_type)
    return await storage.save_image(io.BytesIO(cleaned), normalized_type, size_limit)


def strip_image_metadata(content: bytes, content_type: str) -> bytes:
    """按声明的图片类型去除元数据；类型不支持或结构损坏时抛出 InvalidPrivateFile。"""
    if content_type == "image/jpeg":
        return _strip_jpeg(content)
    if content_type == "image/png":
        return _strip_png(content)
    raise InvalidPrivateFile("只支持 JPG 或 PNG 图片")


def _strip_jpeg(content: bytes) -> bytes:
    """删除 APP1–APP15（APP2 的 ICC 色彩配置除外）和注释段，扫描数据原样保留。

    读到 EOI 即结束：部分手机会在 EOI 之后追加厂商数据或动态照片视频，一并丢弃。
    """
    if not content.startswith(b"\xff\xd8"):
        raise InvalidPrivateFile("图片不是有效的 JPEG")
    output = bytearray(b"\xff\xd8")
    position = 2
    size = len(content)
    while True:
        if position >= size or content[position] != 0xFF:
            raise InvalidPrivateFile("JPEG 结构不完整")
        # 标记前允许有多个 0xFF 填充字节。
        while position < size and content[position] == 0xFF:
            position += 1
        if position >= size:
            raise InvalidPrivateFile("JPEG 结构不完整")
        marker = content[position]
        position += 1
        if marker == 0xD9:
            output += b"\xff\xd9"
            return bytes(output)
        if marker in _JPEG_STANDALONE:
            output += bytes((0xFF, marker))
            continue
        if position + 2 > size:
            raise InvalidPrivateFile("JPEG 结构不完整")
        (length,) = struct.unpack(">H", content[position : position + 2])
        end = position + length
        if length < 2 or end > size:
            raise InvalidPrivateFile("JPEG 结构不完整")
        payload = content[position + 2 : end]
        is_icc = marker == _JPEG_APP2 and payload.startswith(b"ICC_PROFILE\x00")
        if marker == _JPEG_COM or (0xE1 <= marker <= 0xEF and not is_icc):
            position = end
            continue
        output += bytes((0xFF, marker)) + content[position:end]
        position = end
        if marker == _JPEG_SOS:
            position = _copy_entropy_data(content, position, output)


def _copy_entropy_data(content: bytes, position: int, output: bytearray) -> int:
    """复制扫描段后的熵编码数据，返回下一个标记的位置。

    熵编码数据里的 0xFF 后面跟 0x00（字节填充）或 RST 标记，都属于数据本身；
    遇到其他标记才结束本段。
    """
    size = len(content)
    cursor = position
    while cursor + 1 < size:
        if content[cursor] == 0xFF:
            following = content[cursor + 1]
            if following != 0x00 and following != 0xFF and not 0xD0 <= following <= 0xD7:
                output += content[position:cursor]
                return cursor
        cursor += 1
    raise InvalidPrivateFile("JPEG 结构不完整")


def _strip_png(content: bytes) -> bytes:
    """删除文本、EXIF 与时间块，其余块原样保留；必须完整读到 IEND。"""
    if not content.startswith(_PNG_SIGNATURE):
        raise InvalidPrivateFile("图片不是有效的 PNG")
    output = bytearray(_PNG_SIGNATURE)
    position = len(_PNG_SIGNATURE)
    while position + 8 <= len(content):
        (length,) = struct.unpack(">I", content[position : position + 4])
        chunk_type = content[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(content):
            break
        if chunk_type not in _PNG_DROPPED_CHUNKS:
            output += content[position:end]
        position = end
        if chunk_type == b"IEND":
            return bytes(output)
    raise InvalidPrivateFile("PNG 结构不完整")
