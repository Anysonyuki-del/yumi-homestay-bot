from homestay_bot.domain.errors import OperationRefused


class CustomerPermissionError(PermissionError, OperationRefused):
    """表示客户管理操作未通过管理员权限校验。"""

    status_code = 403


class CustomerNotFoundError(LookupError, OperationRefused):
    """表示客户管理目标不存在或已经失效。"""

    status_code = 404


class CustomerConflictError(ValueError, OperationRefused):
    """表示客户管理请求与当前领域状态冲突。"""

    status_code = 409
