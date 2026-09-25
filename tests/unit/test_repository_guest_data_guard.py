"""仓库守护：客人数据不得进入 GitHub（仓库公开）。

1.41.0 起服务器日志与数据库可以记录客人信息（用户决定「开放1和3」），但「只是不要传到
github」。本测试扫描所有受跟踪的文本文件，出现真实形态的手机号或身份证号即失败；测试
和文档里惯用的虚构号码走白名单。CI 每次都会跑。
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 惯用的虚构号码：只在测试和示例里出现，不对应任何真实客人。
_FICTIONAL_NUMBERS = frozenset(
    {
        "13800138000",
        "13800000000",
        "13900139000",
        "13900000000",
        "13812345678",
        "13700137000",
        "13600136000",
        "13512345000",
        "420106199001011234",
    }
)
# 前后都不能紧挨字母或数字：提交哈希这类十六进制串里常有一段像手机号的数字
# （1.41.0 发版记录里的 96ba14285116300e… 曾误报），真实号码前后是标点、空白或汉字。
_MOBILE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
_IDENTITY = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
# 二进制资源，以及依赖锁文件：锁文件里的哈希值常有连续数字，且不可能含客人数据。
_SKIPPED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".lock"
}


def _tracked_text_files() -> list[Path]:
    """返回 git 受跟踪、且不是二进制资源或依赖锁文件的文件。"""
    names = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [ROOT / name for name in names if Path(name).suffix.lower() not in _SKIPPED_SUFFIXES]


def test_no_real_looking_phone_or_identity_numbers_are_tracked() -> None:
    """受跟踪文件里只允许白名单内的虚构号码。"""
    found: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for pattern in (_MOBILE, _IDENTITY):
            for match in pattern.finditer(text):
                if match.group(0) not in _FICTIONAL_NUMBERS:
                    line = text.count("\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(ROOT)}:{line}")
    assert found == [], f"疑似真实客人号码进入了受跟踪文件：{found[:10]}"


def test_database_log_backup_and_export_files_are_ignored() -> None:
    """数据库、日志、备份与导出文件必须被 .gitignore 挡住。"""
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("*.db", "*.sqlite", "*.log", ".backups/", ".stage/", "*.dump"):
        assert pattern in ignored, pattern


def test_hex_hashes_are_not_mistaken_for_phone_numbers() -> None:
    """提交哈希中的数字段不算号码；中文里夹着的真实形态号码仍能扫出。"""
    assert not _MOBILE.search("发布提交 96ba14285116300e051daff99176423d0af424f5")
    assert _MOBILE.search("客人电话13512345000，请回电")
