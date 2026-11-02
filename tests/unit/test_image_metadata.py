"""公开照片去元数据（小程序住客自助 Spec F4）：按段或块删除，图像数据字节不变。"""

import struct
import zlib

import pytest

from homestay_bot.services.image_metadata import strip_image_metadata
from homestay_bot.services.private_file_storage import InvalidPrivateFile


def _segment(marker: int, payload: bytes) -> bytes:
    """拼一个带长度字段的 JPEG 段。"""
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


# 图像相关段：量化表、帧头、霍夫曼表；内容不需要可解码，只需结构正确。
DQT = _segment(0xDB, b"\x00" + bytes(range(64)))
SOF = _segment(0xC2, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00")
DHT = _segment(0xC4, b"\x00" + b"\x01" + b"\x00" * 15 + b"\x05")
APP0 = _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
ICC = _segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + b"profile-bytes")
EXIF_GPS = _segment(0xE1, b"Exif\x00\x00GPSLatitude=30.5")
XMP = _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:GPS>")
OTHER_APP2 = _segment(0xE2, b"MPF\x00multi-picture")
COMMENT = _segment(0xFE, b"shot by alice")
SOS = _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
# 熵编码数据含字节填充（FF 00）与重启标记（FF D0），二者都属于数据本身。
SCAN_1 = b"\x12\x34\xff\x00\x56\xff\xd0\x78"
SCAN_2 = b"\x9a\xff\x00\xbc"


def _jpeg(*segments: bytes, trailer: bytes = b"") -> bytes:
    """按给定段拼出两次扫描的渐进式 JPEG，EOI 后可附加厂商数据。"""
    return (
        b"\xff\xd8"
        + b"".join(segments)
        + DQT
        + SOF
        + DHT
        + SOS
        + SCAN_1
        + DHT
        + SOS
        + SCAN_2
        + b"\xff\xd9"
        + trailer
    )


def test_jpeg_drops_location_xmp_comments_and_trailer_but_keeps_image_bytes() -> None:
    """EXIF、XMP、注释、非 ICC 的 APP2 与 EOI 后数据全部去掉；ICC 与图像段原样保留。"""
    original = _jpeg(APP0, EXIF_GPS, ICC, XMP, OTHER_APP2, COMMENT, trailer=b"MOTION-VIDEO")
    stripped = strip_image_metadata(original, "image/jpeg")
    assert stripped == _jpeg(APP0, ICC)
    for leaked in (b"GPS", b"alice", b"MOTION", b"multi-picture"):
        assert leaked not in stripped


def test_jpeg_without_metadata_is_unchanged() -> None:
    """没有元数据的文件逐字节不变。"""
    original = _jpeg(APP0)
    assert strip_image_metadata(original, "image/jpeg") == original


@pytest.mark.parametrize(
    "broken",
    [
        b"\xff\xd8" + EXIF_GPS[:-3],  # 段长度超出文件
        _jpeg(APP0)[:-2],  # 扫描数据后没有 EOI
        b"\xff\xd8\x00\x00",  # 段之间不是标记
        b"GIF89a",  # 不是 JPEG
    ],
    ids=["segment-overflow", "missing-eoi", "garbage", "not-jpeg"],
)
def test_broken_jpeg_is_rejected_not_saved_as_is(broken: bytes) -> None:
    """结构不完整时拒绝，而不是原样保存可能带元数据的内容。"""
    with pytest.raises(InvalidPrivateFile):
        strip_image_metadata(broken, "image/jpeg")


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    """拼一个带 CRC 的 PNG 块。"""
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data))
    )


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IHDR = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
IDAT = _chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
IEND = _chunk(b"IEND", b"")


def test_png_drops_text_exif_and_time_chunks() -> None:
    """文本、EXIF 与时间块去掉，图像块逐字节保留。"""
    original = (
        PNG_SIGNATURE
        + IHDR
        + _chunk(b"tEXt", b"Author\x00alice")
        + _chunk(b"iTXt", b"GPS\x00\x00\x00\x00\x0030.5")
        + _chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"x"))
        + _chunk(b"eXIf", b"MM\x00*GPS")
        + _chunk(b"tIME", b"\x07\xea\x0b\x03\x00\x00\x00")
        + IDAT
        + IEND
    )
    assert strip_image_metadata(original, "image/png") == PNG_SIGNATURE + IHDR + IDAT + IEND


@pytest.mark.parametrize(
    "broken",
    [PNG_SIGNATURE + IHDR + IDAT, PNG_SIGNATURE + IHDR + IDAT[:-5] + IEND, b"\x89PNX"],
    ids=["missing-iend", "chunk-overflow", "not-png"],
)
def test_broken_png_is_rejected(broken: bytes) -> None:
    """读不到 IEND 或块长度越界时拒绝。"""
    with pytest.raises(InvalidPrivateFile):
        strip_image_metadata(broken, "image/png")


def test_unsupported_type_is_rejected() -> None:
    """只接受 JPG 与 PNG。"""
    with pytest.raises(InvalidPrivateFile):
        strip_image_metadata(b"RIFF....WEBP", "image/webp")
