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
    CustomerContextSummary,
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
async def test_admin_can_edit_customer_crm_with_safe_audits() -> None:
    """CRM 写操作必须复核管理员身份，且审计不得复制备注或摘要正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-crm",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        customer = Customer(display_name="待维护客户")
        tag = CustomerTag(name="VIP", wecom_tag_id="et-vip")
        session.add_all([administrator, customer, tag])
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        added, removed, _revision = await repository.replace_tags(
            customer.id,
            [tag.id],
            administrator.id,
        )
        await repository.update_note(
            customer.id,
            "客户明确要求安静房间",
            administrator.id,
        )
        await repository.update_summary(
            customer_id=customer.id,
            administrator_id=administrator.id,
            short_summary="偏好安静",
            long_summary="过往咨询正文不应进入审计",
            unresolved_items=["待确认到店时间"],
        )
        await session.commit()

        link = await session.scalar(
            select(CustomerTagLink).where(
                CustomerTagLink.customer_id == customer.id,
                CustomerTagLink.tag_id == tag.id,
            )
        )
        summary = await session.scalar(
            select(CustomerContextSummary).where(
                CustomerContextSummary.customer_id == customer.id
            )
        )
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.target_id == str(customer.id)
                    )
                )
            ).all()
        )

        assert added == [tag.id]
        assert removed == []
        assert link is not None
        assert link.sync_pending is True
        assert summary is not None
        assert summary.version == 1
        assert customer.note == "客户明确要求安静房间"
        audit_payload = repr([audit.details for audit in audits])
        assert "客户明确要求安静房间" not in audit_payload
        assert "偏好安静" not in audit_payload
        assert "过往咨询正文不应进入审计" not in audit_payload

    await engine.dispose()


@pytest.mark.asyncio
async def test_staff_cannot_edit_customer_crm() -> None:
    """普通员工即使知道客户编号也不能修改 CRM。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        staff = Employee(
            wecom_userid="staff-crm",
            name="普通员工",
            role=EmployeeRole.STAFF,
        )
        customer = Customer(display_name="受保护客户")
        session.add_all([staff, customer])
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        with pytest.raises(PermissionError):
            await repository.update_note(
                customer.id,
                "越权修改",
                staff.id,
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_reject_merge_without_exposing_customer_text() -> None:
    """拒绝合并只结束建议，并以最小字段写审计。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-reject",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        source = Customer(display_name="来源客户", note="来源私密备注")
        target = Customer(display_name="目标客户", note="目标私密备注")
        session.add_all([administrator, source, target])
        await session.flush()
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="verified_phone",
        )
        session.add(suggestion)
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        await repository.review_merge(
            suggestion.id,
            administrator.id,
            accepted=False,
        )
        await session.commit()

        await session.refresh(suggestion)
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "customer_merge_rejected"
            )
        )
        assert suggestion.status is CustomerMergeStatus.REJECTED
        assert source.merged_into_customer_id is None
        assert audit is not None
        assert audit.details == {"suggestion_id": suggestion.id}
        assert "私密备注" not in repr(audit.details)

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
async def test_manual_merge_suggestion_validates_and_reuses_pending() -> None:
    """手动建议复核管理员和两侧客户，并复用同方向未决建议。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-manual-merge",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        staff = Employee(
            wecom_userid="staff-manual-merge",
            name="普通员工",
            role=EmployeeRole.STAFF,
        )
        inactive_administrator = Employee(
            wecom_userid="inactive-admin-manual-merge",
            name="停用管理员",
            role=EmployeeRole.ADMIN,
            is_active=False,
        )
        source = Customer(display_name="来源客户")
        target = Customer(display_name="目标客户")
        merged = Customer(
            display_name="已合并客户",
            merged_into_customer_id=target.id,
        )
        session.add_all(
            [
                administrator,
                staff,
                inactive_administrator,
                source,
                target,
            ]
        )
        await session.flush()
        merged.merged_into_customer_id = target.id
        session.add(merged)
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        with pytest.raises(PermissionError):
            await repository.create_manual_merge_suggestion(
                source.id,
                target.id,
                staff.id,
            )
        with pytest.raises(ValueError):
            await repository.create_manual_merge_suggestion(
                source.id,
                source.id,
                administrator.id,
            )
        with pytest.raises(PermissionError):
            await repository.create_manual_merge_suggestion(
                source.id,
                target.id,
                inactive_administrator.id,
            )
        with pytest.raises(LookupError):
            await repository.create_manual_merge_suggestion(
                source.id,
                999_999,
                administrator.id,
            )
        with pytest.raises(LookupError):
            await repository.create_manual_merge_suggestion(
                merged.id,
                target.id,
                administrator.id,
            )

        first_id = await repository.create_manual_merge_suggestion(
            source.id,
            target.id,
            administrator.id,
        )
        second_id = await repository.create_manual_merge_suggestion(
            source.id,
            target.id,
            administrator.id,
        )
        await session.commit()

        suggestion = await session.get(CustomerMergeSuggestion, first_id)
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "customer_manual_merge_suggested"
            )
        )
        assert second_id == first_id
        assert suggestion is not None
        assert suggestion.reason == "administrator_manual"
        assert suggestion.status is CustomerMergeStatus.PENDING
        assert audit is not None
        assert audit.details == {
            "source_customer_id": source.id,
            "target_customer_id": target.id,
            "suggestion_id": suggestion.id,
        }

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
            note="来源备注",
        )
        target = Customer(
            display_name="订单客户",
            phone_ciphertext=cipher.encrypt("13800000000"),
            phone_fingerprint=cipher.fingerprint("13800000000"),
            note="目标备注",
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
        source_summary = CustomerContextSummary(
            customer_id=source.id,
            short_summary="来源短摘要",
            long_summary="来源长摘要",
            unresolved_items=["待确认到店时间", "重复事项"],
            version=2,
        )
        target_summary = CustomerContextSummary(
            customer_id=target.id,
            short_summary="目标短摘要",
            long_summary="目标长摘要",
            unresolved_items=["重复事项", "待确认早餐"],
            version=3,
        )
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="verified_phone",
        )
        other_target = Customer(display_name="其他目标")
        session.add_all(
            [
                source_summary,
                target_summary,
                suggestion,
                other_target,
            ]
        )
        await session.flush()
        other_suggestion = CustomerMergeSuggestion(
            source_customer_id=other_target.id,
            target_customer_id=source.id,
            reason="verified_phone",
        )
        session.add(other_suggestion)
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        merged = await repository.merge_locked(
            suggestion.id,
            administrator.id,
        )
        repeated = await repository.merge_locked(
            suggestion.id,
            administrator.id,
        )
        await session.commit()

        await session.refresh(source)
        await session.refresh(conversation)
        await session.refresh(suggestion)
        await session.refresh(order)
        await session.refresh(task)
        await session.refresh(target_summary)
        await session.refresh(other_suggestion)
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
        assert repeated.id == target.id
        assert target.display_name == "订单客户"
        assert target.note == "目标备注\n\n来自合并档案：\n来源备注"
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
        assert target_summary.short_summary == (
            "目标短摘要\n\n来自合并档案：\n来源短摘要"
        )
        assert target_summary.long_summary == (
            "目标长摘要\n\n来自合并档案：\n来源长摘要"
        )
        assert target_summary.unresolved_items == [
            "重复事项",
            "待确认早餐",
            "待确认到店时间",
        ]
        assert other_suggestion.status is CustomerMergeStatus.REJECTED
        assert other_suggestion.reason == "source_customer_merged"
        assert audit is not None
        assert audit.details == {
            "source_customer_id": source.id,
            "target_customer_id": target.id,
            "suggestion_id": suggestion.id,
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_merge_inherits_phone_and_moves_source_only_summary() -> None:
    """目标缺少资料时继承来源电话，并把来源唯一摘要转到目标。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-inherit-merge",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        source = Customer(
            display_name="来源客户",
            phone_ciphertext=cipher.encrypt("13800000000"),
            phone_fingerprint=cipher.fingerprint("13800000000"),
            note="来源备注",
        )
        target = Customer(display_name="目标客户")
        session.add_all([administrator, source, target])
        await session.flush()
        summary = CustomerContextSummary(
            customer_id=source.id,
            short_summary="来源短摘要",
            long_summary="来源长摘要",
            unresolved_items=["待确认入住人数"],
        )
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="administrator_manual",
        )
        session.add_all([summary, suggestion])
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        await repository.merge_locked(suggestion.id, administrator.id)
        await session.commit()
        await session.refresh(summary)

        assert target.display_name == "目标客户"
        assert target.phone_ciphertext == source.phone_ciphertext
        assert target.phone_fingerprint == source.phone_fingerprint
        assert target.note == "来源备注"
        assert summary.customer_id == target.id
        assert summary.short_summary == "来源短摘要"
        assert summary.long_summary == "来源长摘要"
        assert summary.unresolved_items == ["待确认入住人数"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_merge_changes_are_rolled_back_by_outer_transaction() -> None:
    """外层事务回滚后，合并涉及的全部关系仍归来源客户。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-rollback-merge",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        source = Customer(display_name="来源客户", note="来源备注")
        target = Customer(display_name="目标客户", note="目标备注")
        identity = CustomerIdentity(
            customer=source,
            provider=CustomerIdentityProvider.WECOM_KF,
            external_id="wm-rollback-source",
            is_verified=True,
        )
        conversation = Conversation(
            customer=source,
            open_kfid="wk-rollback",
            external_userid="wm-rollback-source",
        )
        session.add_all(
            [administrator, source, target, identity, conversation]
        )
        await session.flush()
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="administrator_manual",
        )
        session.add(suggestion)
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        await repository.merge_locked(suggestion.id, administrator.id)
        await session.rollback()

        await session.refresh(source)
        await session.refresh(target)
        await session.refresh(identity)
        await session.refresh(conversation)
        await session.refresh(suggestion)
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "customer_merge"
            )
        )
        assert source.merged_into_customer_id is None
        assert target.note == "目标备注"
        assert identity.customer_id == source.id
        assert conversation.customer_id == source.id
        assert suggestion.status is CustomerMergeStatus.PENDING
        assert audit_count == 0

    await engine.dispose()
