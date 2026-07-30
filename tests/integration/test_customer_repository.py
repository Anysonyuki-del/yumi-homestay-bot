from datetime import UTC, date, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    CustomerIdentityProvider,
    CustomerMergeStatus,
    EmployeeRole,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BusinessTask,
    Conversation,
    Customer,
    CustomerIdentity,
    CustomerMergeSuggestion,
    CustomerTag,
    CustomerTagLink,
    Employee,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.services.customer_service import CustomerService
from homestay_bot.services.message_service import IncomingMessage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


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


@pytest.mark.asyncio
async def test_ensure_for_message_is_idempotent() -> None:
    """重复处理同一联系人消息不得重复建立客户或身份。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    message = IncomingMessage(
        msgid="msg-1",
        open_kfid="wk-1",
        external_userid="wm-idempotent",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="你好",
        sent_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    async with factory() as session:
        service = CustomerService(
            SQLAlchemyCustomerRepository(session),
            SensitiveDataCipher(Fernet.generate_key().decode("ascii")),
        )
        first = await service.ensure_for_message(message)
        second = await service.ensure_for_message(message)
        await session.commit()

        customer_count = await session.scalar(select(func.count(Customer.id)))
        identity_count = await session.scalar(
            select(func.count(CustomerIdentity.id))
        )
        assert first.id == second.id
        assert customer_count == 1
        assert identity_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_confirm_merge_moves_existing_links_and_writes_safe_audit() -> None:
    """管理员确认后应原子迁移现有关系并写不含客户正文的审计。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-1",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        source = Customer(
            display_name="微信客户",
            phone_ciphertext=cipher.encrypt("13800000000"),
            phone_fingerprint=cipher.fingerprint("13800000000"),
        )
        target = Customer(
            display_name="订单客户",
            phone_ciphertext=cipher.encrypt("13800000000"),
            phone_fingerprint=cipher.fingerprint("13800000000"),
        )
        source_identity = CustomerIdentity(
            customer=source,
            provider=CustomerIdentityProvider.WECOM_KF,
            external_id="wm-source",
            is_verified=True,
        )
        target_identity = CustomerIdentity(
            customer=target,
            provider=CustomerIdentityProvider.HOSTEX,
            external_id="hostex-target",
            is_verified=True,
        )
        tag = CustomerTag(name="老客户")
        source_tag = CustomerTagLink(customer=source, tag=tag)
        conversation = Conversation(
            customer=source,
            open_kfid="wk-1",
            external_userid="wm-source",
        )
        property_profile = PropertyProfile(id=101, title="测试房间")
        session.add_all(
            [
                administrator,
                source,
                target,
                source_identity,
                target_identity,
                tag,
                source_tag,
                conversation,
                property_profile,
            ]
        )
        await session.flush()
        order = StayOrder(
            hostex_reservation_code="R-MERGE",
            stay_code="S-MERGE",
            customer_id=source.id,
            property_id=property_profile.id,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            status="confirmed",
        )
        task = BusinessTask(
            dedupe_key="manual:merge-test",
            task_type=BusinessTaskType.SUPPLIES,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            customer_id=source.id,
            property_id=property_profile.id,
            service_date=date(2026, 8, 1),
            description="补矿泉水",
        )
        session.add_all([order, task])
        await session.flush()
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="verified_phone",
        )
        session.add(suggestion)
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        merged = await repository.merge_locked(
            suggestion.id,
            administrator.id,
        )
        await session.commit()

        await session.refresh(source)
        await session.refresh(conversation)
        await session.refresh(suggestion)
        await session.refresh(order)
        await session.refresh(task)
        identities = list(
            (
                await session.scalars(
                    select(CustomerIdentity).where(
                        CustomerIdentity.customer_id == target.id
                    )
                )
            ).all()
        )
        links = list(
            (
                await session.scalars(
                    select(CustomerTagLink).where(
                        CustomerTagLink.customer_id == target.id
                    )
                )
            ).all()
        )
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "customer_merge")
        )

        assert merged.id == target.id
        assert source.merged_into_customer_id == target.id
        assert conversation.customer_id == target.id
        assert {identity.external_id for identity in identities} == {
            "wm-source",
            "hostex-target",
        }
        assert [link.tag_id for link in links] == [tag.id]
        assert suggestion.status is CustomerMergeStatus.ACCEPTED
        assert suggestion.reviewed_by == administrator.id
        assert order.customer_id == target.id
        assert task.customer_id == target.id
        assert audit is not None
        assert audit.details == {
            "source_customer_id": source.id,
            "target_customer_id": target.id,
            "suggestion_id": suggestion.id,
        }

    await engine.dispose()
