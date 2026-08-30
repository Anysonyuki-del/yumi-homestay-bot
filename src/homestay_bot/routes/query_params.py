"""后台 GET 查询参数的共享规范化规则。"""


def empty_query_to_none(value: object) -> object | None:
    """把浏览器原生表单提交的空值转换为未启用筛选。"""
    if isinstance(value, str) and not value.strip():
        return None
    return value
