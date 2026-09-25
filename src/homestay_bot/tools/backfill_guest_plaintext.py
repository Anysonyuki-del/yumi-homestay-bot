"""一次性回填：把预订审批与客户手机号的存量密文解密，写入 1.41.0 新增的明文列。

用户 2026-09-26 决定数据库可以明文保存客人信息、不再到期清除（「开放1和3」）。迁移
`0028_guest_plaintext` 只加空列，因为解密需要启动配置里的数据密钥，迁移阶段拿不到；
存量记录由本工具在 API 容器内回填。

用法（在 API 容器内执行）：

    python -m homestay_bot.tools.backfill_guest_plaintext           # 只预览条数
    python -m homestay_bot.tools.backfill_guest_plaintext --apply   # 写库

只填明文为空的记录，重复执行不会覆盖；已到期清除的旧审批（pii_purged_at 已设置）没有
密文可解，跳过；解密失败的记录只计数、不中断整批。输出只含条数，不打印客人信息。

ponytail: 一次性工具，整批在一个事务里完成；按当前数据量（审批与客户各在千条以内）足够，
量级变大时改为分批提交。
"""

import argparse
import asyncio
import sys

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import BookingApproval, Customer
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.sensitive_data import SensitiveDataCipher


async def backfill(
    session: AsyncSession,
    cipher: SensitiveDataCipher,
    *,
    apply: bool,
) -> dict[str, int]:
    """统计并（apply 时）回填明文列，返回各类条数；提交与回滚由调用方决定。"""
    sensitive = ApprovalSensitiveData(cipher)
    counts = {"approvals": 0, "customers": 0, "failed": 0}
    approvals = await session.scalars(
        select(BookingApproval).where(
            BookingApproval.pii_purged_at.is_(None),
            BookingApproval.guest_name.is_(None),
            BookingApproval.guest_name_ciphertext.is_not(None),
        )
    )
    for approval in approvals:
        try:
            values = sensitive.read(approval)
        except (InvalidToken, ValueError):
            counts["failed"] += 1
            continue
        counts["approvals"] += 1
        if apply:
            approval.guest_name = values.guest_name
            approval.guest_mobile = values.guest_mobile
            approval.special_requests = values.special_requests
    customers = await session.scalars(
        select(Customer).where(Customer.phone.is_(None), Customer.phone_ciphertext.is_not(None))
    )
    for customer in customers:
        assert customer.phone_ciphertext is not None
        try:
            phone = cipher.decrypt(customer.phone_ciphertext)
        except InvalidToken:
            counts["failed"] += 1
            continue
        counts["customers"] += 1
        if apply:
            customer.phone = phone
    if apply:
        await session.flush()
    return counts


async def _main(apply: bool) -> int:
    """读取启动配置里的数据密钥，在一个事务里预览或回填。"""
    from homestay_bot.config import BootstrapSettings
    from homestay_bot.db import create_engine, create_session_factory

    settings = BootstrapSettings()  # type: ignore[call-arg]
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session:
            counts = await backfill(
                session, SensitiveDataCipher(settings.data_encryption_key), apply=apply
            )
            if apply:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()
    mode = "已回填" if apply else "预览（未写库）"
    print(
        f"{mode}：审批 {counts['approvals']} 条，客户手机号 {counts['customers']} 条，"
        f"解密失败 {counts['failed']} 条"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口：默认预览，--apply 才写库。"""
    parser = argparse.ArgumentParser(description="回填客人资料明文列（1.41.0）")
    parser.add_argument("--apply", action="store_true", help="写库；不加则只预览条数")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.apply))


if __name__ == "__main__":
    sys.exit(main())
