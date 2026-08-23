import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

from homestay_bot import version

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_declared_release_version_is_one_zero_one() -> None:
    """正式基线版本必须由 pyproject 单点声明。"""
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert project["project"]["version"] == "1.0.1"


def test_app_version_reads_installed_distribution_metadata(monkeypatch) -> None:
    """运行时版本必须读取构建产物元数据，不能维护第二份常量。"""
    version.get_app_version.cache_clear()
    monkeypatch.setattr(version, "package_version", lambda name: f"1.2.3:{name}")
    try:
        assert version.get_app_version() == "1.2.3:homestay-bot"
        assert version.get_app_version_label() == "v1.2.3:homestay-bot"
    finally:
        version.get_app_version.cache_clear()


def test_app_version_marks_uninstalled_source_as_development(monkeypatch) -> None:
    """源码未安装时必须明确显示开发态，不能回退成过期发布版本。"""

    def missing_distribution(_name: str) -> str:
        """模拟当前源码没有安装包元数据。"""
        raise PackageNotFoundError

    version.get_app_version.cache_clear()
    monkeypatch.setattr(version, "package_version", missing_distribution)
    try:
        assert version.get_app_version() == "development"
        assert version.get_app_version_label() == "development"
    finally:
        version.get_app_version.cache_clear()
