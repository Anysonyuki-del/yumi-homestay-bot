class CustomerPermissionError(PermissionError):
    """表示客户管理操作未通过管理员权限校验。"""


class CustomerNotFoundError(LookupError):
    """表示客户管理目标不存在或已经失效。"""


class CustomerConflictError(ValueError):
    """表示客户管理请求与当前领域状态冲突。"""
