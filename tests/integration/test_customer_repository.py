from datetime import UTC, date, datetime
from types import SimpleNamespace

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


class _RecordedScalarResult:
    """为锁语句测试返回固定客户集合。"""

    def __init__(self, values: list[object]) -> None:
        """保存即将由 all() 返回的对象。"""
        self._values = values

    def all(self) -> list[object]:
        """返回固定查询结果。"""
        return self._values


class _RecordingMergeSession:
    """记录仓储发出的查询，并提供创建建议所需最小会话行为。"""

    def __init__(self) -> None:
        """构造管理员、两个客户及语句记录容器。"""
        self.administrator = SimpleNamespace(
            id=1,
            role=EmployeeRole.ADMIN,
            is_active=True,
        )
        self.customers = [
            SimpleNamespace(id=7, merged_into_customer_id=None),
            SimpleNamespace(id=8, merged_into_customer_id=None),
        ]
        self.scalar_statements: list[object] = []
        self.scalars_statements: list[object] = []
        self.added: list[object] = []

    async def scalar(self, statement):
        """依次返回管理员和不存在的同方向建议。"""
        self.scalar_statements.append(statement)
        if len(self.scalar_statements) == 1:
            return self.administrator
        return None

    async def scalars(self, statement):
        """记录客户锁查询并返回两个有效客户。"""
        self.scalars_statements.append(statement)
        return _RecordedScalarResult(self.customers)

    def add(self, value) -> None:
        """记录新增建议或审计对象。"""
        self.added.append(value)

    async def flush(self) -> None:
        """模拟数据库为新建议生成主键。"""
        for value in self.added:
            if isinstance(value, CustomerMergeSuggestion) and value.id is None:
                value.id = 41


class _StopAfterSuggestionLocks(RuntimeError):
    """表示锁序测试已记录到建议锁，无需继续执行合并写入。"""


class _RecordingMergeLockSession:
    """记录确认合并路径的加锁实体和客户锁顺序。"""

    def __init__(self, source_id: int, target_id: int) -> None:
        """按指定方向构造建议快照与两个客户。"""
        self.administrator = SimpleNamespace(
            id=1,
            role=EmployeeRole.ADMIN,
            is_active=True,
        )
        self.suggestion = SimpleNamespace(
            id=31,
            source_customer_id=source_id,
            target_customer_id=target_id,
            status=CustomerMergeStatus.PENDING,
        )
        self.customers = [
            SimpleNamespace(id=customer_id, merged_into_customer_id=None)
            for customer_id in sorted([source_id, target_id])
        ]
        self.locked_entities: list[type[object]] = []
        self.customer_order_by: tuple[str, ...] = ()
        self.suggestion_order_by: tuple[str, ...] = ()
        self.locked_customer_ids: tuple[int, ...] = ()

    async def scalar(self, statement):
        """返回无锁建议快照或管理员，并记录旧式逐条锁行为。"""
        entity = statement.column_descriptions[0]["entity"]
        is_locked = statement._for_update_arg is not None
        if entity is CustomerMergeSuggestion:
            if is_locked:
                self.locked_entities.append(entity)
                raise _StopAfterSuggestionLocks
            return self.suggestion
        if entity is Employee:
            if is_locked:
                self.locked_entities.append(entity)
            return self.administrator
        if entity is Customer:
            if is_locked:
                self.locked_entities.append(entity)
            customer_id = (
                self.suggestion.target_customer_id
                if len(self.locked_entities) == 3
                else self.suggestion.source_customer_id
            )
            return next(
                item for item in self.customers if item.id == customer_id
            )
        return None

    async def scalars(self, statement):
        """记录批量客户锁和建议锁，并在锁齐建议后停止。"""
        entity = statement.column_descriptions[0]["entity"]
        if statement._for_update_arg is not None:
            self.locked_entities.append(entity)
        if entity is Customer:
            self.customer_order_by = tuple(
                str(clause) for clause in statement._order_by_clauses
            )
            self.locked_customer_ids = tuple(
                item.id for item in self.customers
            )
            return _RecordedScalarResult(self.customers)
        if entity is CustomerMergeSuggestion:
            self.suggestion_order_by = tuple(
                str(clause) for clause in statement._order_by_clauses
            )
            raise _StopAfterSuggestionLocks
        return _RecordedScalarResult([])


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
async def test_merge_detail_returns_only_safe_association_counts() -> None:
    """合并复核只查询两侧关联数量，不加载消息、订单或任务正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        source = Customer(
            display_name="来源客户",
            note="REPOSITORY_SOURCE_SECRET_NOTE",
            phone_ciphertext=b"source-secret-ciphertext",
        )
        target = Customer(
            display_name="目标客户",
            note="REPOSITORY_TARGET_SECRET_NOTE",
            phone_ciphertext=b"target-secret-ciphertext",
        )
        property_profile = PropertyProfile(id=101, title="测试房源")
        session.add_all([source, target, property_profile])
        await session.flush()
        session.add_all(
            [
                CustomerIdentity(
                    customer_id=source.id,
                    provider=CustomerIdentityProvider.WECOM_KF,
                    external_id="wm-count-source",
                    is_verified=True,
                ),
                Conversation(
                    customer_id=source.id,
                    open_kfid="wk-count",
                    external_userid="wm-count-source",
                ),
                StayOrder(
                    hostex_reservation_code="count-order",
                    stay_code="count-stay",
                    customer_id=target.id,
                    property_id=property_profile.id,
                    check_in_date=date(2026, 8, 1),
                    check_out_date=date(2026, 8, 2),
                    status="confirmed",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.PENDING_CONFIRMATION,
                    customer_id=target.id,
                    description="不应加载的任务正文",
                    checklist={},
                ),
            ]
        )
        await session.flush()
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="administrator_manual",
        )
        session.add(suggestion)
        await session.commit()

        detail = await SQLAlchemyCustomerRepository(session).merge_detail(
            suggestion.id
        )

        assert detail["source"] == {
            "id": source.id,
            "display_name": "来源客户",
        }
        assert detail["target"] == {
            "id": target.id,
            "display_name": "目标客户",
        }
        assert not isinstance(detail["source"], Customer)
        assert not isinstance(detail["target"], Customer)
        serialized = repr(detail)
        assert "REPOSITORY_SOURCE_SECRET_NOTE" not in serialized
        assert "REPOSITORY_TARGET_SECRET_NOTE" not in serialized
        assert "source-secret-ciphertext" not in serialized
        assert "target-secret-ciphertext" not in serialized
        assert detail["source_counts"] == {
            "identities": 1,
            "conversations": 1,
            "orders": 0,
            "tasks": 0,
        }
        assert detail["target_counts"] == {
            "identities": 0,
            "conversations": 0,
            "orders": 1,
            "tasks": 1,
        }
        assert "不应加载的任务正文" not in repr(
            [detail["source_counts"], detail["target_counts"]]
        )

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
async def test_manual_merge_locks_administrator_and_both_customers() -> None:
    """手动建议的管理员查询和两侧客户查询都必须请求行锁。"""
    session = _RecordingMergeSession()
    repository = SQLAlchemyCustomerRepository(session)  # type: ignore[arg-type]

    suggestion_id = await repository.create_manual_merge_suggestion(7, 8, 1)

    assert suggestion_id == 41
    assert session.scalar_statements[0]._for_update_arg is not None
    assert session.scalars_statements[0]._for_update_arg is not None
    assert tuple(
        str(clause)
        for clause in session.scalars_statements[0]._order_by_clauses
    ) == ("customers.id",)
    assert session.scalar_statements[1]._for_update_arg is not None
    assert tuple(
        str(clause)
        for clause in session.scalar_statements[1]._order_by_clauses
    ) == ("customer_merge_suggestions.id",)
    assert [
        session.scalar_statements[0].column_descriptions[0]["entity"],
        session.scalars_statements[0].column_descriptions[0]["entity"],
        session.scalar_statements[1].column_descriptions[0]["entity"],
    ] == [Employee, Customer, CustomerMergeSuggestion]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_id", "target_id"),
    [(7, 8), (8, 7)],
)
async def test_merge_uses_same_lock_hierarchy_for_reverse_directions(
    source_id: int,
    target_id: int,
) -> None:
    """共享客户的正反合并都按员工、客户主键、建议主键顺序加锁。"""
    session = _RecordingMergeLockSession(source_id, target_id)
    repository = SQLAlchemyCustomerRepository(session)  # type: ignore[arg-type]

    with pytest.raises(_StopAfterSuggestionLocks):
        await repository.merge_locked(31, 1)

    assert session.locked_entities == [
        Employee,
        Customer,
        CustomerMergeSuggestion,
    ]
    assert session.customer_order_by == ("customers.id",)
    assert session.locked_customer_ids == (7, 8)
    assert session.suggestion_order_by == (
        "customer_merge_suggestions.id",
    )


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
            short_summary="短" * 4100,
            long_summary="长" * 8100,
            unresolved_items=[
                "重复事项",
                *[f"事项-{index}" for index in range(25)],
                "重复事项",
            ],
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
        assert summary.short_summary == "短" * 4000
        assert summary.long_summary == "长" * 8000
        assert summary.unresolved_items == [
            "重复事项",
            *[f"事项-{index}" for index in range(19)],
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_merge_keeps_target_phone_and_limits_merged_note() -> None:
    """双方电话不同时保留目标电话，并把合并备注限制在两千字。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-phone-priority",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        source = Customer(
            display_name="来源客户",
            phone_ciphertext=cipher.encrypt("13800000000"),
            phone_fingerprint=cipher.fingerprint("13800000000"),
            note="来源" * 100,
        )
        target_phone = cipher.encrypt("13900000000")
        target_fingerprint = cipher.fingerprint("13900000000")
        target_note = "目标" * 995
        target = Customer(
            display_name="目标客户",
            phone_ciphertext=target_phone,
            phone_fingerprint=target_fingerprint,
            note=target_note,
        )
        session.add_all([administrator, source, target])
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
        await session.commit()

        assert target.phone_ciphertext == target_phone
        assert target.phone_fingerprint == target_fingerprint
        assert target.note is not None
        assert len(target.note) == 2000
        assert target.note.startswith(f"{target_note}\n\n来自合并档案：\n")

    await engine.dispose()


@pytest.mark.asyncio
async def test_replayed_merge_resolves_current_final_customer() -> None:
    """A 合并到 B、B 再合并到 C 后，重放 A 到 B 应直接返回 C。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        administrator = Employee(
            wecom_userid="admin-chain-merge",
            name="YuMi",
            role=EmployeeRole.ADMIN,
        )
        customer_a = Customer(display_name="客户 A", note="A 备注")
        customer_b = Customer(display_name="客户 B", note="B 备注")
        customer_c = Customer(display_name="客户 C", note="C 备注")
        session.add_all(
            [administrator, customer_a, customer_b, customer_c]
        )
        await session.flush()
        suggestion_ab = CustomerMergeSuggestion(
            source_customer_id=customer_a.id,
            target_customer_id=customer_b.id,
            reason="administrator_manual",
        )
        suggestion_bc = CustomerMergeSuggestion(
            source_customer_id=customer_b.id,
            target_customer_id=customer_c.id,
            reason="administrator_manual",
        )
        session.add_all([suggestion_ab, suggestion_bc])
        await session.commit()

        repository = SQLAlchemyCustomerRepository(session)
        await repository.merge_locked(suggestion_ab.id, administrator.id)
        await session.commit()
        await repository.merge_locked(suggestion_bc.id, administrator.id)
        await session.commit()
        note_after_two_merges = customer_c.note

        replayed = await repository.merge_locked(
            suggestion_ab.id,
            administrator.id,
        )
        await session.commit()

        assert replayed.id == customer_c.id
        assert customer_c.note == note_after_two_merges

    await engine.dispose()


@pytest.mark.asyncio
async def test_merge_changes_are_rolled_back_when_final_flush_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """末次 flush 异常并由外层回滚后，全部关系和正文保持原状。"""
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
        tag = CustomerTag(name="回滚标签")
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
            [
                administrator,
                source,
                target,
                tag,
                identity,
                conversation,
            ]
        )
        await session.flush()
        tag_link = CustomerTagLink(customer_id=source.id, tag_id=tag.id)
        property_profile = PropertyProfile(id=202, title="回滚测试房间")
        session.add_all([tag_link, property_profile])
        await session.flush()
        order = StayOrder(
            hostex_reservation_code="R-MERGE-ROLLBACK",
            stay_code="S-MERGE-ROLLBACK",
            customer_id=source.id,
            property_id=property_profile.id,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            status="confirmed",
        )
        task = BusinessTask(
            dedupe_key="manual:merge-rollback",
            task_type=BusinessTaskType.SUPPLIES,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            customer_id=source.id,
            property_id=property_profile.id,
            service_date=date(2026, 8, 1),
            description="回滚测试任务",
        )
        summary = CustomerContextSummary(
            customer_id=source.id,
            short_summary="来源短摘要",
            long_summary="来源长摘要",
            unresolved_items=["来源待确认项"],
        )
        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="administrator_manual",
        )
        session.add_all([order, task, summary, suggestion])
        await session.commit()

        async def fail_final_flush() -> None:
            """在仓储完成所有内存和数据库改动后模拟最终写入失败。"""
            raise RuntimeError("injected final flush failure")

        repository = SQLAlchemyCustomerRepository(session)
        monkeypatch.setattr(session, "flush", fail_final_flush)
        with pytest.raises(RuntimeError, match="injected final flush failure"):
            await repository.merge_locked(suggestion.id, administrator.id)
        await session.rollback()

        await session.refresh(source)
        await session.refresh(target)
        await session.refresh(identity)
        await session.refresh(conversation)
        await session.refresh(order)
        await session.refresh(task)
        await session.refresh(tag_link)
        await session.refresh(summary)
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
        assert order.customer_id == source.id
        assert task.customer_id == source.id
        assert tag_link.customer_id == source.id
        assert summary.customer_id == source.id
        assert summary.short_summary == "来源短摘要"
        assert summary.long_summary == "来源长摘要"
        assert summary.unresolved_items == ["来源待确认项"]
        assert suggestion.status is CustomerMergeStatus.PENDING
        assert audit_count == 0

    await engine.dispose()
