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
    RoomOccupancyStatus,
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

        async def list_room_stays(
            self,
            start_date: date,
            end_date: date,
        ) -> tuple[StayRecord, ...]:
            """当前场景不需要订单。"""
            return ()

        async def list_open_task_counts(
            self,
            local_date: date,
        ) -> tuple[RoomTaskCountRecord, ...]:
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


async def test_snapshot_groups_repeated_business_tasks_into_one_work_queue() -> None:
    """待确认业务任务应汇总为工作队列，避免历史任务铺满关注页。"""

    class RepositoryStub:
        """提供重复业务任务并实现运营服务所需的空房态查询。"""

        async def prepare_consistent_read(self) -> None:
            """测试仓储无需准备事务。"""

        async def list_attention(self) -> tuple[AttentionRecord, ...]:
            """返回三条等待管理员确认的业务任务。"""
            return tuple(
                AttentionRecord(
                    kind="task",
                    record_id=record_id,
                    status=BusinessTaskStatus.PENDING_CONFIRMATION,
                    property_id=None,
                    room_title=None,
                    updated_at=datetime(2026, 8, record_id, tzinfo=UTC),
                )
                for record_id in (1, 2, 3)
            )

        async def list_active_rooms(self) -> tuple[ActiveRoomRecord, ...]:
            """当前场景不需要房间矩阵。"""
            return ()

        async def list_room_stays(
            self,
            start_date: date,
            end_date: date,
        ) -> tuple[StayRecord, ...]:
            """当前场景不需要订单。"""
            return ()

        async def list_open_task_counts(
            self,
            local_date: date,
        ) -> tuple[RoomTaskCountRecord, ...]:
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
    assert snapshot.attention_items[0].target_url == "/employee/tasks"


async def test_snapshot_counts_manual_reminder_and_derived_task_once() -> None:
    """同一次提醒失败及其人工任务只能形成一个待关注事项。"""
    engine, factory = await _factory()
    today = date(2026, 8, 29)
    async with factory() as session:
        room = PropertyProfile(id=7, title="江景房", is_active=True)
        order = StayOrder(
            hostex_reservation_code="reservation-one",
            stay_code="stay-one",
            property_id=7,
            check_in_date=today,
            check_out_date=today + timedelta(days=1),
            status="accepted",
        )
        session.add_all([room, order])
        await session.flush()
        reminder = LifecycleReminder(
            order_id=order.id,
            reminder_type=ReminderType.ARRIVAL_DAY,
            scheduled_local_date=today,
            scheduled_at=datetime(2026, 8, 29, 2, tzinfo=UTC),
            status=ReminderStatus.MANUAL_FOLLOWUP,
        )
        session.add(reminder)
        await session.flush()
        session.add(
            BusinessTask(
                dedupe_key=f"lifecycle-manual:{reminder.id}",
                task_type=BusinessTaskType.MANUAL_CONTACT,
                status=BusinessTaskStatus.PENDING_CONFIRMATION,
                order_id=order.id,
                property_id=room.id,
                service_date=today,
                description="人工联系",
            )
        )
        await session.commit()

        snapshot = await AdminOperationsService(session).snapshot(
            datetime(2026, 8, 29, 3, tzinfo=UTC)
        )

    assert snapshot.attention_count == 1
    assert len(snapshot.attention_items) == 1
    assert snapshot.attention_items[0].kind == "task"
    await engine.dispose()  # type: ignore[attr-defined]


async def test_snapshot_batches_room_recent_operations_and_next_actions() -> None:
    """近期运营板应批量聚合入住事实、任务风险与下一步。"""
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
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.EXPIRED,
                    property_id=1,
                    service_date=today - timedelta(days=1),
                    description="已失效任务",
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
            datetime(2026, 8, 28, 16, 30, tzinfo=UTC),
            horizon_days=3,
            source_synced_at=datetime(2026, 8, 28, 16, tzinfo=UTC),
        )

    assert snapshot.local_date == today
    assert [room.room_title for room in snapshot.rooms] == ["一号房", "二号房"]
    first, second = snapshot.rooms
    assert first.status is RoomOperationalStatus.OCCUPIED
    assert first.occupancy_status is RoomOccupancyStatus.ARRIVING_TODAY
    assert first.today_arrival_count == 1
    assert first.today_departure_count == 0
    assert first.open_task_count == 1
    assert first.overdue_task_count == 0
    assert first.next_arrival == today
    assert first.next_departure == today + timedelta(days=2)
    assert "入住" in first.next_action
    assert second.status is RoomOperationalStatus.NOT_STARTED
    assert second.occupancy_status is RoomOccupancyStatus.VACANT
    assert second.today_arrival_count == 0
    assert second.next_arrival == today + timedelta(days=3)
    assert snapshot.horizon_days == 3
    assert snapshot.source_stale is False
    assert len(snapshot.seven_day_rooms) == 2
    assert len(snapshot.seven_day_rooms[0].days) == 6
    assert snapshot.seven_day_rooms[0].days[2].arrival_count == 1
    assert snapshot.seven_day_rooms[0].days[2].occupied is True
    assert snapshot.seven_day_rooms[0].days[4].occupied is False
    assert snapshot.seven_day_rooms[1].days[5].arrival_count == 1
    assert query_count <= 10
    await engine.dispose()  # type: ignore[attr-defined]


async def test_snapshot_marks_occupancy_unknown_when_hostex_sync_is_stale() -> None:
    """同步心跳超过六小时后不得把本地订单投影冒充实时房态。"""
    engine, factory = await _factory()
    observed_at = datetime(2026, 8, 29, 3, tzinfo=UTC)
    today = date(2026, 8, 29)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=1, title="一号房", is_active=True),
                StayOrder(
                    hostex_reservation_code="stale-one",
                    stay_code="stale-one",
                    property_id=1,
                    check_in_date=today,
                    check_out_date=today + timedelta(days=1),
                    status="confirmed",
                ),
            ]
        )
        await session.commit()

        snapshot = await AdminOperationsService(session).snapshot(
            observed_at,
            source_synced_at=observed_at - timedelta(hours=7),
        )

    assert snapshot.source_stale is True
    assert snapshot.rooms[0].occupancy_status is RoomOccupancyStatus.UNKNOWN
    assert snapshot.rooms[0].next_action == "先确认百居易实时房态"
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


async def test_snapshot_orders_rooms_by_operational_risk_before_stable_rooms() -> None:
    """房间顺序必须按今日周转、逾期任务和运营准备排列，稳定房间排在最后。"""
    engine, factory = await _factory()
    today = date(2026, 8, 29)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=1, room_number="0101", title="A 稳定房", is_active=True),
                PropertyProfile(id=2, room_number="0201", title="B 周转房", is_active=True),
                PropertyProfile(id=3, room_number="0301", title="C 逾期房", is_active=True),
                PropertyProfile(id=4, room_number="0401", title="D 维修房", is_active=True),
                RoomOperationalState(property_id=1, status=RoomOperationalStatus.READY),
                RoomOperationalState(property_id=4, status=RoomOperationalStatus.MAINTENANCE),
                StayOrder(
                    hostex_reservation_code="turnover-out",
                    stay_code="turnover-out",
                    property_id=2,
                    check_in_date=today - timedelta(days=2),
                    check_out_date=today,
                    status="confirmed",
                ),
                StayOrder(
                    hostex_reservation_code="turnover-in",
                    stay_code="turnover-in",
                    property_id=2,
                    check_in_date=today,
                    check_out_date=today + timedelta(days=2),
                    status="confirmed",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.ASSIGNED,
                    property_id=3,
                    service_date=today - timedelta(days=2),
                    description="逾期保洁",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                    property_id=3,
                    service_date=today - timedelta(days=1),
                    description="逾期检查",
                ),
            ]
        )
        await session.commit()

        snapshot = await AdminOperationsService(session).snapshot(
            datetime(2026, 8, 28, 16, 30, tzinfo=UTC),
            horizon_days=3,
            source_synced_at=datetime(2026, 8, 28, 16, tzinfo=UTC),
        )

    assert [room.room_title for room in snapshot.rooms] == [
        "B 周转房",
        "C 逾期房",
        "D 维修房",
        "A 稳定房",
    ]
    assert [room.room_title for room in snapshot.attention_rooms] == [
        "B 周转房",
        "C 逾期房",
        "D 维修房",
    ]
    assert [room.room_title for room in snapshot.stable_rooms] == ["A 稳定房"]
    assert snapshot.attention_room_count == 3
    for room in snapshot.rooms:
        timeline = snapshot.timeline_for(room.property_id)
        assert timeline is not None
        assert timeline.room_title == room.room_title
    await engine.dispose()  # type: ignore[attr-defined]


async def test_snapshot_exposes_timeline_span_including_past_days() -> None:
    """时间轴同时包含已过去的两天，快照必须给出可直接展示的真实起止日期。"""
    engine, factory = await _factory()
    today = date(2026, 8, 29)
    async with factory() as session:
        session.add(PropertyProfile(id=1, title="一号房", is_active=True))
        await session.commit()

        snapshot = await AdminOperationsService(session).snapshot(
            datetime(2026, 8, 28, 16, 30, tzinfo=UTC),
            horizon_days=7,
            source_synced_at=datetime(2026, 8, 28, 16, tzinfo=UTC),
        )

    assert snapshot.timeline_start_date == today - timedelta(days=2)
    assert snapshot.timeline_end_date == today + timedelta(days=7)
    assert len(snapshot.seven_day_rooms[0].days) == 10
    assert snapshot.seven_day_rooms[0].days[0].local_date == snapshot.timeline_start_date
    assert snapshot.seven_day_rooms[0].days[-1].local_date == snapshot.timeline_end_date
    await engine.dispose()  # type: ignore[attr-defined]


async def test_stale_source_keeps_every_room_in_the_attention_group() -> None:
    """房态不可信时不得把任何房间降级为稳定房间。"""
    engine, factory = await _factory()
    observed_at = datetime(2026, 8, 29, 3, tzinfo=UTC)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=1, title="一号房", is_active=True),
                RoomOperationalState(property_id=1, status=RoomOperationalStatus.READY),
            ]
        )
        await session.commit()

        snapshot = await AdminOperationsService(session).snapshot(
            observed_at,
            source_synced_at=observed_at - timedelta(hours=7),
        )

    assert snapshot.source_stale is True
    assert snapshot.stable_rooms == ()
    assert [room.room_title for room in snapshot.attention_rooms] == ["一号房"]
    await engine.dispose()  # type: ignore[attr-defined]
