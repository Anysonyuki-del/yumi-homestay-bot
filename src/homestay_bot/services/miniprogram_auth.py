import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.domain.enums import CustomerIdentityProvider
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.repositories.miniprogram_sessions import SQLAlchemyMiniProgramSessionRepository

MINIPROGRAM_SESSION_TTL = timedelta(hours=24)
MAX_SESSIONS_PER_IDENTITY = 5
EXPIRED_CLEANUP_LIMIT = 100
LOGIN_WINDOW_SECONDS = 60.0
LOGIN_WINDOW_LIMIT = 30
LOGIN_MAX_IN_FLIGHT = 8
# 不申请昵称、头像或手机号权限，新档案只用通用名称；人工合并后显示目标客户名称。
MINIPROGRAM_DISPLAY_NAME = "小程序客户"


class WeChatCodeExchanger(Protocol):
    """定义认证服务所需的微信 code 交换能力。"""

    @property
    def app_id(self) -> str:
        """返回身份命名空间使用的 AppID。"""

    async def exchange_code(self, code: str) -> str:
        """校验一次性 code 并返回 OpenID。"""


class MiniProgramLoginThrottled(Exception):
    """表示全局登录窗口或在途名额已满，本次请求未调用微信。"""


@dataclass(frozen=True)
class MiniProgramLogin:
    """登录成功后返回给客户端的最小结果；token 明文只在这里出现一次。"""

    token: str
    expires_at: datetime
    display_name: str


@dataclass(frozen=True)
class MiniProgramProfile:
    """已登录客人可见的最小资料，不含 OpenID 或内部客户编号。"""

    expires_at: datetime
    display_name: str


class MiniProgramLoginGate:
    """登录的全局滑动窗口与在途上限，与员工登录额度互不影响。

    ponytail: 只按全局计数而不按 IP：当前 uvicorn 未信任代理转发头，经反代进入时
    客户端 IP 很可能都是代理地址，按 IP 计数会让一个人的刷量挡住所有客人。状态只在
    单进程内存中；改为多实例部署或确认取得真实客户端 IP 后，需要改为共享存储或按 IP 计数。
    """

    def __init__(
        self,
        *,
        window_seconds: float = LOGIN_WINDOW_SECONDS,
        window_limit: int = LOGIN_WINDOW_LIMIT,
        max_in_flight: int = LOGIN_MAX_IN_FLIGHT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化空窗口与在途计数。"""
        self._window_seconds = window_seconds
        self._window_limit = window_limit
        self._max_in_flight = max_in_flight
        self._clock = clock
        self._attempts: deque[float] = deque()
        self._in_flight = 0

    def try_acquire(self) -> bool:
        """检查并占用一个名额；整个判断没有 await，在单事件循环内是原子的。"""
        now = self._clock()
        while self._attempts and self._attempts[0] <= now - self._window_seconds:
            self._attempts.popleft()
        if self._in_flight >= self._max_in_flight or len(self._attempts) >= self._window_limit:
            return False
        self._attempts.append(now)
        self._in_flight += 1
        return True

    def release(self) -> None:
        """归还在途名额；窗口计数保留到自然过期。"""
        self._in_flight = max(0, self._in_flight - 1)


def hash_token(token: str) -> str:
    """数据库只保存 token 的 SHA-256 摘要；token 本身是高熵随机数，无需加盐慢哈希。"""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class MiniProgramAuthService:
    """编排微信 code 交换与短数据库事务，签发、核验和撤销小程序会话。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        exchanger: WeChatCodeExchanger,
        gate: MiniProgramLoginGate | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """注入会话工厂、微信客户端、登录闸门与可测试时钟。"""
        self._factory = factory
        self._exchanger = exchanger
        self._gate = gate or MiniProgramLoginGate()
        self._clock = clock

    async def login(self, code: str) -> MiniProgramLogin:
        """先在事务外向微信校验，再在一个事务内建档并签发会话，提交成功才返回 token。"""
        if not self._gate.try_acquire():
            raise MiniProgramLoginThrottled
        try:
            # 外部调用期间不打开数据库事务，避免慢速上游占用连接或持锁。
            openid = await self._exchanger.exchange_code(code)
        finally:
            self._gate.release()

        external_id = f"{self._exchanger.app_id}:{openid}"
        token = secrets.token_urlsafe(32)
        now = self._clock()
        expires_at = now + MINIPROGRAM_SESSION_TTL
        async with self._factory() as session, session.begin():
            customer = await SQLAlchemyCustomerRepository(session).ensure_identity(
                provider=CustomerIdentityProvider.WECHAT_MINIPROGRAM,
                external_id=external_id,
                display_name=MINIPROGRAM_DISPLAY_NAME,
            )
            display_name = customer.display_name
            sessions = SQLAlchemyMiniProgramSessionRepository(session)
            identity_id = await sessions.identity_id_for(external_id)
            if identity_id is None:
                # ensure_identity 刚返回客户却查不到身份，只可能是数据被并发改动；
                # 不签发无法解析的会话，让本次登录失败由客户端重新取 code。
                raise RuntimeError("小程序身份建档后不可读取")
            await sessions.create(
                identity_id=identity_id,
                token_hash=hash_token(token),
                now=now,
                expires_at=expires_at,
                max_per_identity=MAX_SESSIONS_PER_IDENTITY,
                expired_cleanup_limit=EXPIRED_CLEANUP_LIMIT,
            )
        return MiniProgramLogin(token=token, expires_at=expires_at, display_name=display_name)

    async def profile(self, token: str) -> MiniProgramProfile | None:
        """核验 token 并返回当前客户最小资料；无效或过期返回 None。"""
        async with self._factory() as session:
            resolved = await SQLAlchemyMiniProgramSessionRepository(session).resolve(
                hash_token(token),
                self._clock(),
            )
        if resolved is None:
            return None
        customer, expires_at = resolved
        return MiniProgramProfile(expires_at=expires_at, display_name=customer.display_name)

    async def logout(self, token: str) -> None:
        """撤销当前会话；提交失败时异常上抛，调用方不得声称已退出。"""
        async with self._factory() as session, session.begin():
            await SQLAlchemyMiniProgramSessionRepository(session).revoke(hash_token(token))
