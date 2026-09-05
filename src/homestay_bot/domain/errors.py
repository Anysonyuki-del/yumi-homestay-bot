"""定义可以安全展示给使用者的领域异常。"""


class OperationRefused(Exception):
    """业务规则拒绝了本次操作，且消息是刻意写给使用者看的。

    页面默认不回显任何异常原文，因为 SQL 片段、文件路径和凭据都可能出现在
    异常文本里。继承本类型是唯一的例外：只有当消息本身就是为使用者撰写、且
    不含内部细节时才使用它，其余异常继续只记录类型与追踪号。

    抛出时请写清楚被拒的原因和可执行的下一步，而不是复述规则名称。
    """

    status_code = 409

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        return_to: str | None = None,
    ) -> None:
        """记录展示文案、对应状态，以及失败后应当回到的页面。

        回跳路径由调用方显式给出：本应用发送 `referrer-policy: no-referrer`，
        浏览器不会带 Referer，靠请求头推断来源必然落到兜底地址。
        """
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.return_to = return_to
