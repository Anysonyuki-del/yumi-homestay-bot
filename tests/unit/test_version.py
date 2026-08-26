import re
import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

from homestay_bot import version

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_declared_release_version_is_valid_semantic_version() -> None:
    """唯一版本源必须保持可发布的三段式语义版本格式。"""
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    declared_version = project["project"]["version"]

    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", declared_version)


def test_default_sqlite_driver_is_a_runtime_dependency() -> None:
    """默认 SQLite 部署必须在不安装开发依赖时也能启动。"""
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert any(
        dependency.startswith("aiosqlite")
        for dependency in project["project"]["dependencies"]
    )


def test_long_lived_docs_do_not_copy_current_release_version() -> None:
    """README 和长期手册只能指向版本源，不能手写当前发布号。"""
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    declared_version = project["project"]["version"]
    readme = (PROJECT_ROOT / "README.md").read_text()
    handbook = (PROJECT_ROOT / "YuMi民宿AI开发经验与防回归手册.md").read_text()

    assert declared_version not in readme
    assert declared_version not in handbook
    assert "当前稳定发布版本" not in readme
    assert "\nversion:" not in handbook


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
