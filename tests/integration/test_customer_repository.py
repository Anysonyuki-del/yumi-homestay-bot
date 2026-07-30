import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    CustomerIdentityProvider,
    CustomerMergeStatus,
)
from homestay_bot.domain.models import (
    Base,
    Conversation,
    Customer,
    CustomerIdentity,
    CustomerMergeSuggestion,
    CustomerTag,
    CustomerTagLink,
)


@pytest.mark.asyncio
async def test_customer_identity_and_conversation_share_customer() -> None:
    """客户身份、标签和会话必须稳定关联到同一个客户主档。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="微信客户")
        identity = CustomerIdentity(
            customer=customer,
            provider=CustomerIdentityProvider.WECOM_KF,
            external_id="wm-1",
            is_verified=True,
        )
        tag = CustomerTag(name="老客户", wecom_tag_id="et-tag-1")
        link = CustomerTagLink(customer=customer, tag=tag)
        session.add_all([customer, identity, tag, link])
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="wk-1",
            external_userid="wm-1",
        )
        session.add(conversation)
        await session.commit()

        assert conversation.customer_id == customer.id
        assert identity.customer_id == customer.id
        assert link.sync_pending is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_customer_identity_provider_and_external_id_are_unique() -> None:
    """同一渠道身份只能属于一个客户，避免重复建立正式档案。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                CustomerIdentity(
                    customer=Customer(display_name="客户一"),
                    provider=CustomerIdentityProvider.WECOM_KF,
                    external_id="wm-duplicate",
                    is_verified=True,
                ),
                CustomerIdentity(
                    customer=Customer(display_name="客户二"),
                    provider=CustomerIdentityProvider.WECOM_KF,
                    external_id="wm-duplicate",
                    is_verified=True,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_customer_merge_suggestion_defaults_to_pending() -> None:
    """可靠身份匹配只能形成待管理员确认的合并建议。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        source = Customer(display_name="微信客户")
        target = Customer(display_name="订单客户")
        session.add_all([source, target])
        await session.flush()
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="verified_phone",
        )
        session.add(suggestion)
        await session.commit()

        assert suggestion.status is CustomerMergeStatus.PENDING
        assert suggestion.reviewed_by is None

    await engine.dispose()
