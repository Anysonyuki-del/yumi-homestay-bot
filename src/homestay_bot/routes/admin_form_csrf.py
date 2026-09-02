"""后台业务表单的服务端一次性令牌助手。

令牌曾按实体编号累积写入签名会话 Cookie，只在提交成功时删除，浏览详情页而不提交
即永久驻留；条目足够多时 `Set-Cookie` 会超出浏览器 4096 字节上限，整个会话被丢弃，
表现为随机掉登录和「表单令牌无效或已使用」。这里把令牌全部搬到服务端，会话不再存放
任何令牌。

用途取 `family:entity_id`，让服务端作用域按实体隔离：顺序浏览大量详情页不会挤占
同一个作用域，同一实体反复打开则由淘汰保证不在 GET 阶段 429。
"""

from datetime import timedelta
from typing import Protocol, cast

from fastapi import HTTPException, Request, status

from homestay_bot.services.admin_csrf import AdminCsrfCapacityError

# 运营表单对齐 SESSION_IDLE_TIMEOUT，保持迁移前「令牌与会话同寿」的行为。
OPERATIONS_FORM_TTL = timedelta(hours=8)

TASK_CSRF_FAMILY = "task-write"
PROPERTY_CSRF_FAMILY = "property-write"
COMPLAINT_CSRF_FAMILY = "complaint-write"
CUSTOMER_CSRF_FAMILY = "customer-write"
CUSTOMER_MERGE_CSRF_FAMILY = "customer-merge"
# 审批确认会创建真实订单，保持服务端默认的十五分钟，超时强制刷新重读当前状态。
APPROVAL_CSRF_FAMILY = "approval-confirm"


class AdminCsrfServicePort(Protocol):
    """定义后台表单所需的服务端一次性 nonce 接口。"""

    async def issue(
        self,
        purpose: str,
        *,
        admin_id: int | None,
        evict_oldest_in_scope: bool = False,
        ttl: timedelta | None = None,
    ) -> str:
        """签发绑定用途与管理员主体的一次性令牌。"""

    async def consume(
        self,
        token: str,
        purpose: str,
        *,
        admin_id: int | None,
    ) -> bool:
        """原子消费匹配的有效令牌。"""


def get_csrf_service(request: Request) -> AdminCsrfServicePort:
    """读取服务端 nonce 服务，避免把可覆盖的 Cookie 当作安全真值。"""
    service = getattr(request.app.state, "admin_csrf_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="表单安全服务尚未配置",
        )
    return cast(AdminCsrfServicePort, service)


def csrf_subject(request: Request) -> int:
    """读取已由员工会话复核过的管理员主体编号。"""
    admin_id = request.session.get("admin_id")
    if not isinstance(admin_id, int):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员尚未登录",
        )
    return admin_id


def _purpose(family: str, entity_id: int) -> str:
    """按家族和实体编号生成服务端作用域用途。"""
    return f"{family}:{entity_id}"


async def issue_form_csrf(
    request: Request,
    *,
    family: str,
    entity_id: int,
    ttl: timedelta | None = OPERATIONS_FORM_TTL,
) -> str:
    """为单个实体的写操作签发服务端一次性令牌。"""
    try:
        return await get_csrf_service(request).issue(
            _purpose(family, entity_id),
            admin_id=csrf_subject(request),
            evict_oldest_in_scope=True,
            ttl=ttl,
        )
    except AdminCsrfCapacityError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="表单请求过于频繁",
        ) from error


async def consume_form_csrf(
    request: Request,
    *,
    family: str,
    entity_id: int,
    token: str,
    detail: str = "表单令牌无效或已使用",
) -> None:
    """校验并原子消费令牌；用途绑定实体，跨实体重放必然失败。

    `detail` 允许调用方保留自己的用户可见文案，例如审批确认页的「确认令牌」。
    """
    consumed = await get_csrf_service(request).consume(
        token,
        _purpose(family, entity_id),
        admin_id=csrf_subject(request),
    )
    if not consumed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def drop_legacy_session_key(request: Request, key: str) -> None:
    """清除迁移前遗留的会话令牌字典，让既有臃肿 Cookie 立即收缩。"""
    if key in request.session:
        del request.session[key]
