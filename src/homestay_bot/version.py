"""集中读取应用发布版本，避免页面、诊断与接口各自维护编号。"""

from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

_DISTRIBUTION_NAME = "homestay-bot"
_DEVELOPMENT_VERSION = "development"


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """从已安装包元数据读取唯一版本；源码未安装时明确标记开发态。"""
    try:
        return package_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _DEVELOPMENT_VERSION


def get_app_version_label() -> str:
    """返回适合后台界面展示的版本标签。"""
    version = get_app_version()
    return version if version == _DEVELOPMENT_VERSION else f"v{version}"
