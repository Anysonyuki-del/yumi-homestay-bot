from datetime import UTC, date, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    ComplaintReviewStatus,
    CredentialDeliveryStatus,
    CustomerMergeStatus,
    ReminderStatus,
    ReminderType,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    ComplaintReview,
    Conversation,
    CredentialDelivery,
    Customer,
    CustomerMergeSuggestion,
    LifecycleReminder,
    PropertyProfile,
    RoomCredential,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.repositories.admin_operations import (
    ActiveRoomRecord,
    AttentionRecord,
    RoomTaskCountRecord,
    StayRecord,
)
from homestay_bot.services.admin_operations_service import AdminOperationsService


async def _factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    """创建每个测试独立的内存数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_snapshot_keeps_attention_statuses_separate_and_returns_safe_links() -> None:
    """五类人工事项应保留领域状态，且摘要不得暴露客户或凭证内容。"""
    engine, factory = await _factory()
    today = date(2026, 8, 29)
    async with factory() as session:
        room = PropertyProfile(id=7, room_number="0701", title="江景大床房", is_active=True)
        customer_a = Customer(display_name="敏感客户甲")
        customer_b = Customer(display_name="敏感客户乙")
        conversation = Conversation(open_kfid="kf-secret", external_userid="guest-secret")
        session.add_all([room, customer_a, customer_b, conversation])
        await session.flush()
        order = StayOrder(
            hostex_reservation_code="reservation-secret",
            stay_code="stay-secret",
            property_id=room.id,
            check_in_date=today,
            check_out_date=today + timedelta(days=2),
            status="confirmed",
        )
        credential = RoomCredential(
            property_id=room.id,
            version=3,
            password_ciphertext=b"password-secret",
            guide_ciphertext=b"guide-secret",
            qr_file_id="qr-secret",
            is_active=True,
        )
        session.add_all([order, credential])
        await session.flush()
        session.add_all(
            [
                ComplaintReview(
                    conversation_id=conversation.id,
                    source_message_id="message-secret",
                    reason="service_issue",
                    risk_level="high",
                    status=ComplaintReviewStatus.DELIVERY_FAILED,
                    analysis={"raw": "private-message"},
                    draft="private-draft",
                ),
                CredentialDelivery(
                    order_id=order.id,
                    credential_id=credential.id,
                    status=CredentialDeliveryStatus.NEEDS_REVIEW,
                ),
                LifecycleReminder(
                    order_id=order.id,
                    reminder_type=ReminderType.ARRIVAL_DAY,
                    scheduled_local_date=today,
                    scheduled_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
                    status=ReminderStatus.MANUAL_FOLLOWUP,
                    failure_reason="provider-secret",
                ),
                CustomerMergeSuggestion(
                    source_customer_id=customer_a.id,
                    target_customer_id=customer_b.id,
                    reason="phone",
                    status=CustomerMergeStatus.PENDING,
                ),
                BusinessTask(
                    task_type=BusinessTaskType.SPECIAL_SERVICE,
                    status=BusinessTaskStatus.PENDING_CONFIRMATION,
                    description="客户要求 secret service",
                ),
            ]
        )
        await session.commit()

        snapshot = await AdminOperationsService(session).snapshot(
            datetime(2026, 8, 28, 16, 30, tzinfo=UTC)
        )

    items = {item.kind: item for item in snapshot.attention_items}
    assert items["complaint"].status is ComplaintReviewStatus.DELIVERY_FAILED
    assert items["credential"].status is CredentialDeliveryStatus.NEEDS_REVIEW
    assert items["reminder"].status is ReminderStatus.MANUAL_FOLLOWUP
    assert items["customer_merge"].status is CustomerMergeStatus.PENDING
    assert items["task"].status is BusinessTaskStatus.PENDING_CONFIRMATION
    assert items["complaint"].target_url.startswith("/employee/complaints/")
    assert items["customer_merge"].target_url.startswith("/employee/customers/merge/")
    assert items["task"].target_url.startswith("/employee/tasks/")
    assert items["credential"].target_url.startswith("/employee/tasks")
    assert items["reminder"].target_url.startswith("/employee/tasks")
    assert all(item.title and item.summary for item in snapshot.attention_items)
    snapshot_text = repr(snapshot)
    for secret in (
        "敏感客户",
        "guest-secret",
        "reservation-secret",
        "password-secret",
        "guide-secret",
        "private-message",
        "private-draft",
        "provider-secret",
        "secret service",
    ):
        assert secret not in snapshot_text
    await engine.dispose()  # type: ignore[attr-defined]


async def test_snapshot_groups_repeated_room_followups_without_hiding_total() -> None:
    """同房源的提醒应汇总为一张卡，同时保留真实待处理数量。"""

    class RepositoryStub:
        """提供重复提醒并实现运营服务所需的空房态查询。"""

        async def prepare_consistent_read(self) -> None:
            """测试仓储无需准备事务。"""

        async def list_attention(self) -> tuple[AttentionRecord, ...]:
            """返回同一房源的三条历史人工提醒。"""
            return tuple(
                AttentionRecord(
                    kind="reminder",
                    record_id=record_id,
                    status=ReminderStatus.MANUAL_FOLLOWUP,
                    property_id=7,
                    room_title="江景大床房",
                    updated_at=datetime(2026, 8, record_id, tzinfo=UTC),
                )
                for record_id in (1, 2, 3)
            )

        async def list_active_rooms(self) -> tuple[ActiveRoomRecord, ...]:
            """当前场景不需要房间矩阵。"""
            return ()

        async def list_current_and_future_stays(
            self,
            local_date: date,
        ) -> tuple[StayRecord, ...]:
            """当前场景不需要订单。"""
            return ()

        async def list_open_task_counts(self) -> tuple[RoomTaskCountRecord, ...]:
            """当前场景不需要任务统计。"""
            return ()

    service = AdminOperationsService(  # type: ignore[arg-type]
        None,
        repository=RepositoryStub(),
    )

    snapshot = await service.snapshot(datetime(2026, 8, 29, tzinfo=UTC))

    assert snapshot.attention_count == 3
    assert len(snapshot.attention_items) == 1
    assert snapshot.attention_items[0].related_count == 3
    assert "3 项" in snapshot.attention_items[0].summary
    assert snapshot.attention_items[0].target_url == "/employee/tasks?property_id=7"


async def test_snapshot_batches_room_today_next_arrival_and_seven_day_facts() -> None:
    """逐房和七日矩阵应批量聚合，并排除停用房间及终止订单。"""
    engine, factory = await _factory()
    query_count = 0

    def count_query(*_: object) -> None:
        """记录 SQL 次数，防止查询数随房间数量增长。"""
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)  # type: ignore[attr-defined]
    today = date(2026, 8, 29)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=1, room_number="0101", title="一号房", is_active=True),
                PropertyProfile(id=2, room_number="0201", title="二号房", is_active=True),
                PropertyProfile(id=3, room_number="0301", title="停用房", is_active=False),
                RoomOperationalState(property_id=1, status=RoomOperationalStatus.OCCUPIED),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.ASSIGNED,
                    property_id=1,
                    service_date=today,
                    description="清洁一号房",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.COMPLETED,
                    property_id=1,
                    service_date=today,
                    description="已完成任务",
                ),
                StayOrder(
                    hostex_reservation_code="active-one",
                    stay_code="active-one",
                    property_id=1,
                    check_in_date=today,
                    check_out_date=today + timedelta(days=2),
                    status="confirmed",
                ),
                StayOrder(
                    hostex_reservation_code="next-two",
                    stay_code="next-two",
                    property_id=2,
                    check_in_date=today + timedelta(days=3),
                    check_out_date=today + timedelta(days=5),
                    status="confirmed",
                ),
                StayOrder(
                    hostex_reservation_code="cancelled-two",
                    stay_code="cancelled-two",
                    property_id=2,
                    check_in_date=today,
                    check_out_date=today + timedelta(days=1),
                    status=" CANCELLED ",
                ),
                StayOrder(
                    hostex_reservation_code="inactive-three",
                    stay_code="inactive-three",
                    property_id=3,
                    check_in_date=today,
                    check_out_date=today + timedelta(days=1),
                    status="confirmed",
                ),
            ]
        )
        await session.commit()
        query_count = 0

        snapshot = await AdminOperationsService(session).snapshot(
            datetime(2026, 8, 28, 16, 30, tzinfo=UTC)
        )

    assert snapshot.local_date == today
    assert [room.room_title for room in snapshot.rooms] == ["一号房", "二号房"]
    first, second = snapshot.rooms
    assert first.status is RoomOperationalStatus.OCCUPIED
    assert first.today_arrival_count == 1
    assert first.today_departure_count == 0
    assert first.open_task_count == 1
    assert first.next_arrival == today
    assert second.status is RoomOperationalStatus.NOT_STARTED
    assert second.today_arrival_count == 0
    assert second.next_arrival == today + timedelta(days=3)
    assert len(snapshot.seven_day_rooms) == 2
    assert len(snapshot.seven_day_rooms[0].days) == 7
    assert snapshot.seven_day_rooms[0].days[0].arrival_count == 1
    assert snapshot.seven_day_rooms[0].days[0].occupied is True
    assert snapshot.seven_day_rooms[0].days[2].occupied is False
    assert snapshot.seven_day_rooms[1].days[3].arrival_count == 1
    assert snapshot.seven_day_rooms[1].days[4].occupied is True
    assert query_count <= 10
    await engine.dispose()  # type: ignore[attr-defined]


async def test_snapshot_returns_complete_empty_collections() -> None:
    """空数据库仍应返回可直接渲染的完整快照。"""
    engine, factory = await _factory()
    async with factory() as session:
        snapshot = await AdminOperationsService(session).snapshot(
            datetime(2026, 8, 29, tzinfo=UTC)
        )

    assert snapshot.local_date == date(2026, 8, 29)
    assert snapshot.attention_items == ()
    assert snapshot.rooms == ()
    assert snapshot.seven_day_rooms == ()
    await engine.dispose()  # type: ignore[attr-defined]
