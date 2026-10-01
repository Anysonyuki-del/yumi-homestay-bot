"""预订审批人工核验回填（W5，Spec `docs/specs/2026-10-01_approval-review-backfill-spec.md`）。

服务层用真实 ApprovalPageService、临时 SQLite 与真实审计；百居易只读查询用替身控制
三种情况（查到疑似订单、查询失败、没有订单），不调用真实接口。
"""

from datetime import UTC, date, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import AuditLog, Base, BookingApproval, Conversation, Employee
from homestay_bot.integrations.hostex_client import Reservation
from homestay_bot.services.approval_page_service import (
    ApprovalActionRefused,
    ApprovalPageService,
)
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.sensitive_data import SensitiveDataCipher

CHECK_IN = date(2026, 10, 10)
CHECK_OUT = date(2026, 10, 12)


class HostexStub:
    """只读百居易替身：reservations 为返回的订单，error 为要抛出的异常。"""

    def __init__(self, reservations=(), error: Exception | None = None) -> None:
        self.reservations = list(reservations)
        self.error = error
        self.queries: list[object] = []

    async def list_reservations(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.reservations


class BookingStub:
    """这些动作都不应走建单流程。"""

    async def confirm_and_create(self, *args, **kwargs):
        raise AssertionError("人工核验动作不应创建订单")


def _reservation(code: str, *, name: str = "张三", phone: str = "13800138000", **overrides):
    """合成百居易订单。"""
    values = {
        "reservation_code": code, "stay_code": f"S-{code}", "property_id": 101,
        "check_in_date": CHECK_IN, "check_out_date": CHECK_OUT, "status": "accepted",
        "guest_name": name, "guest_phone": phone, "created_at": "2026-10-01T02:00:00Z",
    }
    values.update(overrides)
    return Reservation(**values)


def new_sensitive() -> ApprovalSensitiveData:
    """每个用例独立的合成密钥。"""
    return ApprovalSensitiveData(SensitiveDataCipher(Fernet.generate_key().decode("ascii")))


async def seed_world(factory, sensitive) -> None:
    """一张需复核、一张有冲突的合成审批，均带上次确认填写的字段。

    PostgreSQL 版本（test_approval_review_postgresql）复用同一份数据。
    """
    async with factory() as session:
        session.add(Employee(id=1, wecom_userid="synthetic", name="合成管理员",
                             role=EmployeeRole.ADMIN, is_active=True))
        conversation = Conversation(open_kfid="wk", external_userid="wm")
        session.add(conversation)
        await session.flush()
        for approval_id, status in ((1, ApprovalStatus.NEEDS_REVIEW), (2, ApprovalStatus.CONFLICT)):
            approval = BookingApproval(
                id=approval_id, approval_code=f"APP-{approval_id}",
                conversation_id=conversation.id, status=status,
                check_in_date=CHECK_IN, check_out_date=CHECK_OUT, number_of_guests=2,
                room_type_preference="江景房", property_id=101, final_rate_amount=399,
                received_amount=399, income_method_id=1, approved_by=1,
                approved_at=datetime(2026, 10, 1, tzinfo=UTC), hostex_request_id="req-1",
                failure_message="创建结果暂时无法自动核验",
            )
            sensitive.write(approval, guest_name="张三", guest_mobile="13800138000",
                            special_requests=None)
            session.add(approval)
        await session.commit()


@pytest.fixture
async def world():
    """项目自己的引擎与会话工厂上的合成审批。

    用项目引擎：SQLite 的事务、外键与 expire_on_commit 设置与生产装配一致，
    保存点一类的事务语义问题才测得出来（Codex AR5）。
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    sensitive = new_sensitive()
    await seed_world(factory, sensitive)
    yield factory, sensitive
    await engine.dispose()


async def _run(world, action, hostex=None):
    """在独立会话里执行一次动作并提交，返回审批最新状态与审计动作。"""
    factory, sensitive = world
    async with factory() as session:
        service = ApprovalPageService(
            session=session, hostex=hostex or HostexStub(), booking=BookingStub(),
            sensitive_data=sensitive,
        )
        try:
            await action(service)
            await session.commit()
        finally:
            await session.rollback()
    async with factory() as session:
        approvals = {item.id: item for item in await session.scalars(select(BookingApproval))}
        audits = list(await session.scalars(select(AuditLog.action)))
    return approvals, audits


@pytest.mark.asyncio
async def test_backfill_marks_booked_with_the_entered_code(world) -> None:
    """A1：需复核填入订单号直接标为已预订，不调用百居易，审计记下订单号。"""
    hostex = HostexStub(error=AssertionError("A1 不应调用百居易"))

    approvals, audits = await _run(
        world, lambda s: s.backfill_reservation(1, 1, "  HX-20261010-01 "), hostex
    )

    assert approvals[1].status is ApprovalStatus.BOOKED
    assert approvals[1].hostex_reservation_code == "HX-20261010-01"
    assert audits == ["booking_approval_backfilled"]
    assert hostex.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["", "有空格 的", "https://evil.example/x", "-开头"])
async def test_backfill_rejects_codes_that_are_not_order_numbers(world, code) -> None:
    """订单号格式不对时拒绝，审批保持需复核。"""
    with pytest.raises(ApprovalActionRefused):
        await _run(world, lambda s: s.backfill_reservation(1, 1, code))
    approvals, audits = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW and audits == []


async def _noop():
    """只为读取当前状态。"""


@pytest.mark.asyncio
async def test_backfill_refuses_a_code_already_used_by_another_approval(world) -> None:
    """R2：同一订单号不能回填到两张审批。"""
    factory, _ = world
    async with factory() as session:
        other = await session.get(BookingApproval, 2)
        other.status = ApprovalStatus.BOOKED
        other.hostex_reservation_code = "HX-1"
        await session.commit()

    with pytest.raises(ApprovalActionRefused, match="另一张审批"):
        await _run(world, lambda s: s.backfill_reservation(1, 1, "HX-1"))
    approvals, _ = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_backfill_only_for_needs_review(world) -> None:
    """有冲突的审批当时没建单，不能直接填订单号。"""
    with pytest.raises(ApprovalActionRefused, match="状态已经变化"):
        await _run(world, lambda s: s.backfill_reservation(2, 1, "HX-2"))


@pytest.mark.asyncio
async def test_reopen_returns_to_pending_and_clears_last_confirmation(world) -> None:
    """A2：反查没有同一客人同一日期的订单，回到待审批并清空上次确认填写的字段。"""
    hostex = HostexStub([_reservation("OTHER", name="李四", phone="13900000000")])

    approvals, audits = await _run(world, lambda s: s.reopen_after_review(1, 1), hostex)

    reopened = approvals[1]
    assert reopened.status is ApprovalStatus.PENDING
    for field in ("property_id", "final_rate_amount", "received_amount", "income_method_id",
                  "approved_by", "approved_at", "hostex_request_id", "failure_message"):
        assert getattr(reopened, field) is None, field
    assert audits == ["booking_approval_reopened"]
    [query] = hostex.queries
    assert (query.start_check_in_date, query.end_check_in_date) == (CHECK_IN, CHECK_IN)
    assert query.property_id == 101


@pytest.mark.asyncio
async def test_reopen_refused_when_a_matching_order_already_exists(world) -> None:
    """R3：查到同一客人、同一日期的订单（不论订单状态）就不回退，提示改用填入订单号。"""
    hostex = HostexStub([_reservation("HX-FOUND", status="cancelled")])

    with pytest.raises(ApprovalActionRefused, match="HX-FOUND"):
        await _run(world, lambda s: s.reopen_after_review(1, 1), hostex)
    approvals, audits = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW
    assert approvals[1].final_rate_amount == 399 and audits == []


@pytest.mark.asyncio
async def test_reopen_refused_when_the_lookup_fails_or_is_truncated(world) -> None:
    """R3：查询失败或结果取满都按查不清处理，不回退。"""
    with pytest.raises(ApprovalActionRefused, match="暂时查不到"):
        await _run(world, lambda s: s.reopen_after_review(1, 1),
                   HostexStub(error=RuntimeError("upstream down")))
    many = [_reservation(f"X{index}", name=f"客{index}") for index in range(100)]
    with pytest.raises(ApprovalActionRefused, match="订单太多"):
        await _run(world, lambda s: s.reopen_after_review(1, 1), HostexStub(many))
    approvals, _ = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_reopen_refused_when_guest_data_was_purged(world) -> None:
    """客人资料已清理时无法反查，不回退。"""
    factory, _ = world
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        approval.guest_name_ciphertext = None
        approval.guest_name = None
        await session.commit()

    with pytest.raises(ApprovalActionRefused, match="已按保留期清理"):
        await _run(world, lambda s: s.reopen_after_review(1, 1))


@pytest.mark.asyncio
async def test_reopen_refused_if_someone_else_handled_it_during_the_lookup(world) -> None:
    """R5：反查期间别人已处理（例如已拒绝），加锁复核后拒绝回退。"""
    factory, sensitive = world

    class ChangingHostex(HostexStub):
        async def list_reservations(self, query):
            async with factory() as other:
                approval = await other.get(BookingApproval, 1)
                approval.status = ApprovalStatus.REJECTED
                await other.commit()
            return []

    with pytest.raises(ApprovalActionRefused, match="状态已经变化"):
        await _run(world, lambda s: s.reopen_after_review(1, 1), ChangingHostex())
    approvals, _ = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_recheck_moves_conflict_back_to_pending_without_calling_hostex(world) -> None:
    """A3：有冲突回到待审批，不调用百居易；需复核的审批不能走这条路。"""
    hostex = HostexStub(error=AssertionError("A3 不应调用百居易"))

    approvals, audits = await _run(world, lambda s: s.recheck_after_conflict(2, 1), hostex)

    assert approvals[2].status is ApprovalStatus.PENDING
    assert approvals[2].property_id is None
    assert audits == ["booking_approval_recheck"]
    with pytest.raises(ApprovalActionRefused):
        await _run(world, lambda s: s.recheck_after_conflict(1, 1))


# ---- Codex 实施审查 AR1–AR4 的回归（Spec §5）----------------------------------

from contextlib import asynccontextmanager  # noqa: E402
from datetime import timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from homestay_bot.domain.schemas import ConfirmBookingCommand  # noqa: E402
from homestay_bot.integrations.hostex_client import (  # noqa: E402
    AvailabilityDay,
    CreateReservationResult,
    HostexTransportError,
    PropertyAvailability,
)
from homestay_bot.repositories.approvals import (  # noqa: E402
    SQLAlchemyApprovalRepository,
    SQLAlchemyPermissionChecker,
)
from homestay_bot.services.booking_service import BookingService  # noqa: E402


def _command(property_id: int, amount: int) -> ConfirmBookingCommand:
    """合成管理员的明确收款确认。"""
    return ConfirmBookingCommand(
        property_id=property_id, final_rate_amount=amount, received_amount=amount,
        income_method_id=1, payment_confirmed=True,
    )


class BookingHostex:
    """建单用的合成上游：房态全部可用；uncertain 时建单响应丢失、写后查询也失败；
    lookup_fails 时建单成功但写后查询失败。只在内存里记录，绝不发真实请求。"""

    def __init__(self, *, uncertain: bool = False, lookup_fails: bool = False) -> None:
        self.uncertain = uncertain
        self.lookup_fails = lookup_fails
        self.created: list = []

    async def list_availabilities(self, ids, start, end):
        days = []
        while start < end:
            days.append(AvailabilityDay(date=start, available=True, remarks=""))
            start += timedelta(days=1)
        return [PropertyAvailability(property_id=ids[0], days=days)]

    async def create_reservation(self, request):
        self.created.append(request)
        if self.uncertain:
            raise HostexTransportError("synthetic acknowledgement lost")
        return CreateReservationResult(request_id="SYNTHETIC-REQ")

    async def list_reservations(self, query):
        # 建单前（A2 回退前的防重反查）还没有订单；读失败只模拟写后核验那一次。
        if not self.created:
            return []
        if self.uncertain or self.lookup_fails:
            raise HostexTransportError("synthetic read failure")
        request = self.created[-1]
        return [_reservation(
            "SYNTHETIC-BOOKED", name=request.guest_name, phone=request.mobile,
            property_id=request.property_id, created_at=datetime.now(UTC).isoformat(),
            rates={"rate_amount": request.rate_amount},
        )]

    # 审批详情页的参考数据接口，门面装配需要。
    async def list_properties(self):
        return []

    async def list_reference_prices(self, start, end):
        return []

    async def list_income_methods(self):
        return []


def _facade(factory, sensitive, hostex):
    """生产使用的会话门面，只把外部客户端换成合成上游。"""
    from homestay_bot.application import SessionApprovalPageService

    class Registry:
        @asynccontextmanager
        async def acquire(self):
            yield SimpleNamespace(hostex=hostex)

    return SessionApprovalPageService(
        factory=factory, registry=Registry(), sensitive_data=sensitive
    )


@pytest.mark.asyncio
async def test_stale_reopen_cannot_undo_a_newer_confirmation_round(world) -> None:
    """AR1：旧 A2 查询期间，别人完成「回退→重新确认→再次需复核」；旧 A2 必须被拒，
    新一轮的确认字段不被清空，也不再登记第二条回退审计，建单只发生一次。"""
    factory, sensitive = world
    booking_hostex = BookingHostex(uncertain=True)

    class DelayedLookup(HostexStub):
        async def list_reservations(self, query):
            async with factory() as other:
                await ApprovalPageService(
                    session=other, hostex=HostexStub(), booking=BookingStub(),
                    sensitive_data=sensitive,
                ).reopen_after_review(1, 1)
                await other.commit()
            async with factory() as other:
                await BookingService(
                    SQLAlchemyApprovalRepository(other), SQLAlchemyPermissionChecker(other),
                    booking_hostex, sensitive,
                ).confirm_and_create(1, 1, _command(102, 499))
            return []

    with pytest.raises(ApprovalActionRefused, match="已被重新确认过"):
        await _run(world, lambda s: s.reopen_after_review(1, 1), DelayedLookup())

    approvals, audits = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW
    assert (approvals[1].property_id, approvals[1].final_rate_amount) == (102, 499)
    assert audits.count("booking_approval_reopened") == 1
    assert [request.property_id for request in booking_hostex.created] == [102]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "name", "phone", "blocked"),
    [
        # 区号三组用不同姓名，只有手机归一化正确才会命中（Codex AR7）。
        ("+86 写法", "王五", "+86 138-0013-8000", True),
        ("0086 写法", "王五", "008613800138000", True),
        ("86 写法", None, "8613800138000", True),
        ("缺手机号", "张三", None, True),
        ("姓名带空格", " 张 三 ", "13900000000", True),
        ("姓名与手机都缺失", None, None, True),
        ("只有手机一致", "李四", "13800138000", True),
        ("明显是另一位客人", "李四", "13900000000", False),
        # 本身就是 86 开头的 11 位号码，不能被当成区号去掉，否则会与别的号码误配。
        ("86 开头的本地号码", "李四", "86013800138", False),
    ],
)
async def test_reopen_blocks_any_order_that_may_be_the_same_guest(
    world, label, name, phone, blocked
) -> None:
    """AR2：同日期订单只要姓名一致、手机一致或两者都缺失，就挡住回退；明显无关的订单不挡。"""
    hostex = HostexStub([_reservation("HX-SAME-DAY", name=name, phone=phone)])
    if blocked:
        with pytest.raises(ApprovalActionRefused, match="HX-SAME-DAY"):
            await _run(world, lambda s: s.reopen_after_review(1, 1), hostex)
        approvals, _ = await _run(world, lambda s: _noop())
        assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW, label
    else:
        approvals, _ = await _run(world, lambda s: s.reopen_after_review(1, 1), hostex)
        assert approvals[1].status is ApprovalStatus.PENDING, label


@pytest.mark.asyncio
async def test_confirm_through_the_real_session_facade_reaches_booked(world) -> None:
    """AR3（既有缺陷）：经生产会话门面确认，上游建单成功且唯一匹配，最终为已预订，
    订单号与请求编号都持久化，建单恰好一次；A2 回退后再确认同样成功。"""
    factory, sensitive = world
    hostex = BookingHostex()
    facade = _facade(factory, sensitive, hostex)

    await facade.reopen_after_review(1, 1)
    result = await facade.confirm(1, 1, _command(101, 399))

    assert result.status is ApprovalStatus.BOOKED
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        assert approval.status is ApprovalStatus.BOOKED
        assert approval.hostex_reservation_code == "SYNTHETIC-BOOKED"
        assert approval.hostex_request_id == "SYNTHETIC-REQ"
    assert len(hostex.created) == 1

    # 已预订后再次确认不重复建单。
    await facade.confirm(1, 1, _command(101, 399))
    assert len(hostex.created) == 1


@pytest.mark.asyncio
async def test_unverifiable_creation_through_facade_becomes_needs_review_with_request_id(
    world,
) -> None:
    """AR3：建单成功但写后查询失败时转需复核，请求编号仍随同一事务保存，不停在创建中。"""
    factory, sensitive = world
    hostex = BookingHostex(lookup_fails=True)
    facade = _facade(factory, sensitive, hostex)
    await facade.reopen_after_review(1, 1)

    result = await facade.confirm(1, 1, _command(101, 399))

    assert result.status is ApprovalStatus.NEEDS_REVIEW
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        assert (approval.status, approval.hostex_request_id) == (
            ApprovalStatus.NEEDS_REVIEW,
            "SYNTHETIC-REQ",
        )
    assert len(hostex.created) == 1


@pytest.mark.asyncio
async def test_backfill_collision_is_refused_without_partial_writes(world) -> None:
    """AR4/AR5：查重之后另一会话抢先登记同一订单号，转成受控拒绝；会话不提交，
    数据库里审批仍是需复核、没有订单号和审计。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    factory, sensitive = world

    class RaceSession(AsyncSession):
        raced = False

        async def scalar(self, statement, *args, **kwargs):
            result = await super().scalar(statement, *args, **kwargs)
            if not self.raced and "hostex_reservation_code =" in str(statement):
                self.raced = True
                async with factory() as other:
                    approval = await other.get(BookingApproval, 2)
                    approval.status = ApprovalStatus.BOOKED
                    approval.hostex_reservation_code = "HX-RACE"
                    await other.commit()
            return result

    race_factory = async_sessionmaker(factory.kw["bind"], class_=RaceSession,
                                      expire_on_commit=False)
    async with race_factory() as session:
        with pytest.raises(ApprovalActionRefused, match="另一张审批"):
            await ApprovalPageService(
                session=session, hostex=HostexStub(), booking=BookingStub(),
                sensitive_data=sensitive,
            ).backfill_reservation(1, 1, "HX-RACE")

    approvals, audits = await _run(world, lambda s: _noop())
    assert approvals[1].status is ApprovalStatus.NEEDS_REVIEW
    assert approvals[1].hostex_reservation_code is None
    assert "booking_approval_backfilled" not in audits



# ---- Codex 第二轮审查 AR5–AR6 的回归（Spec §6）--------------------------------


async def _approval_state(factory):
    """读审批 1 的状态、订单号、房间与回填审计条数。"""
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        audits = list(await session.scalars(
            select(AuditLog).where(AuditLog.action == "booking_approval_backfilled")
        ))
        return (approval.status, approval.hostex_reservation_code, len(audits))


@pytest.mark.asyncio
async def test_backfill_audit_failure_leaves_nothing_behind(world) -> None:
    """AR5：审计写入失败时，经生产门面的回填整体撤销，不残留已预订或订单号。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    factory, sensitive = world

    class AuditFailureSession(AsyncSession):
        async def flush(self, objects=None):
            if any(isinstance(item, AuditLog) for item in self.new):
                raise RuntimeError("synthetic audit persistence failure")
            return await super().flush(objects)

    failing = async_sessionmaker(factory.kw["bind"], class_=AuditFailureSession,
                                 expire_on_commit=False)
    with pytest.raises(RuntimeError):
        await _facade(failing, sensitive, BookingHostex()).backfill_reservation(1, 1, "HX-AUDIT")

    assert await _approval_state(factory) == (ApprovalStatus.NEEDS_REVIEW, None, 0)


@pytest.mark.asyncio
async def test_backfill_rolled_back_by_the_caller_is_fully_undone(world) -> None:
    """AR5：服务返回后调用方回滚，状态、订单号与审计一起撤销；正常提交时三者一起落库。"""
    factory, sensitive = world
    async with factory() as session:
        await ApprovalPageService(
            session=session, hostex=HostexStub(), booking=BookingStub(), sensitive_data=sensitive,
        ).backfill_reservation(1, 1, "HX-ROLLBACK")
        await session.rollback()
    assert await _approval_state(factory) == (ApprovalStatus.NEEDS_REVIEW, None, 0)

    await _facade(factory, sensitive, BookingHostex()).backfill_reservation(1, 1, "HX-OK")
    assert await _approval_state(factory) == (ApprovalStatus.BOOKED, "HX-OK", 1)


class _InterleavingHostex(BookingHostex):
    """在第一次建单的 await 内插入其他请求；订单在建单返回后才可查询。"""

    def __init__(self, during_first_create) -> None:
        super().__init__()
        self.visible: list = []
        self._during_first_create = during_first_create
        self.fail_first_with: Exception | None = None

    async def create_reservation(self, request):
        self.created.append(request)
        if len(self.created) == 1:
            await self._during_first_create()
            if self.fail_first_with is not None:
                raise self.fail_first_with
        self.visible.append(_reservation(
            f"HX-ROOM-{request.property_id}", name=request.guest_name, phone=request.mobile,
            property_id=request.property_id, created_at=datetime.now(UTC).isoformat(),
            rates={"rate_amount": request.rate_amount},
        ))
        return CreateReservationResult(request_id=f"REQ-{request.property_id}")

    async def list_reservations(self, query):
        return [
            item for item in self.visible
            if query.property_id is None or item.property_id == query.property_id
        ]


@pytest.mark.asyncio
async def test_repeat_confirm_while_creation_is_in_flight_changes_nothing(world) -> None:
    """AR6①：原建单还没返回（不满 5 分钟）时再点确认，保持创建中，A2 也回退不了，
    不会发起第二次建单；原建单返回后正常成为已预订。"""
    factory, sensitive = world
    observed = {}

    async def during_first_create():
        facade = _facade(factory, sensitive, hostex)
        observed["repeat"] = (await facade.confirm(1, 1, _command(101, 399))).status
        with pytest.raises(ApprovalActionRefused):
            await facade.reopen_after_review(1, 1)

    hostex = _InterleavingHostex(during_first_create)
    facade = _facade(factory, sensitive, hostex)
    await facade.reopen_after_review(1, 1)
    result = await facade.confirm(1, 1, _command(101, 399))

    assert observed["repeat"] is ApprovalStatus.CREATING
    assert result.status is ApprovalStatus.BOOKED
    assert [request.property_id for request in hostex.created] == [101]
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        assert (approval.hostex_reservation_code, approval.hostex_request_id) == (
            "HX-ROOM-101", "REQ-101",
        )


async def _age_creating(factory, minutes: int) -> None:
    """把本轮确认时间往前推，模拟原建单卡住超过阈值。"""
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        approval.approved_at = datetime.now(UTC) - timedelta(minutes=minutes)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_outcome", ["success", "rejected"])
async def test_late_result_of_an_old_round_cannot_overwrite_the_new_round(
    world, late_outcome
) -> None:
    """AR6②：原建单卡住超过 5 分钟被恢复、转需复核，管理员回退并完成新一轮（房间 102）；
    旧一轮的成功或失败结果迟到，都不能改动新一轮的状态、房间、金额、订单号与请求编号。"""
    from homestay_bot.integrations.hostex_client import HostexBusinessError

    factory, sensitive = world

    async def during_first_create():
        await _age_creating(factory, minutes=6)
        facade = _facade(factory, sensitive, hostex)
        assert (await facade.confirm(1, 1, _command(101, 399))).status is (
            ApprovalStatus.NEEDS_REVIEW
        )
        await facade.reopen_after_review(1, 1)
        second = await facade.confirm(1, 1, _command(102, 499))
        assert second.status is ApprovalStatus.BOOKED

    hostex = _InterleavingHostex(during_first_create)
    if late_outcome == "rejected":
        hostex.fail_first_with = HostexBusinessError(
            400, "synthetic-req", "synthetic late rejection"
        )
    facade = _facade(factory, sensitive, hostex)
    await facade.reopen_after_review(1, 1)
    await facade.confirm(1, 1, _command(101, 399))

    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        assert approval.status is ApprovalStatus.BOOKED
        assert (approval.property_id, approval.final_rate_amount) == (102, 499)
        assert (approval.hostex_reservation_code, approval.hostex_request_id) == (
            "HX-ROOM-102", "REQ-102",
        )
        assert approval.failure_message is None


# ---- Codex 第三轮审查 AR8–AR9：补足判别力 --------------------------------------


class _MatchingHostex:
    """写后核验替身：按给定房间、创建时间与金额返回一笔与审批客人匹配的订单。"""

    def __init__(self, *, property_id: int, created_at: datetime, rate: int) -> None:
        self._reservation = _reservation(
            f"HX-ROOM-{property_id}", property_id=property_id,
            created_at=created_at.isoformat(), rates={"rate_amount": rate},
        )

    async def list_reservations(self, query):
        return [self._reservation]


async def _set_round(factory, *, approved_at, property_id, amount) -> None:
    """在独立会话里把审批放到某一轮的「创建中」。"""
    async with factory() as session:
        approval = await session.get(BookingApproval, 1)
        approval.status = ApprovalStatus.CREATING
        approval.approved_at = approved_at
        approval.property_id = property_id
        approval.final_rate_amount = amount
        approval.received_amount = amount
        approval.hostex_request_id = None
        approval.hostex_reservation_code = None
        approval.failure_message = None
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_exit", ["reconcile", "needs_review"])
async def test_late_result_cannot_overwrite_a_newer_round_that_is_still_creating(
    world, old_exit
) -> None:
    """AR8：旧一轮结果返回时，新一轮也处在「创建中」、只是确认时间不同——只看状态挡不住，
    必须靠确认时间识别轮次。旧结果的成功和失败出口都被丢弃，新一轮自己的结果照常写入。"""
    factory, sensitive = world
    old_at = datetime.now(UTC) - timedelta(minutes=10)
    new_at = datetime.now(UTC) - timedelta(minutes=1)
    await _set_round(factory, approved_at=old_at, property_id=101, amount=399)

    async with factory() as old_session:
        # 旧一轮的会话持有旧对象；写回出口加锁后会从数据库重读，这里也验证这一点。
        old_approval = await old_session.get(BookingApproval, 1)
        # 真实流程里写回之前，建单前的事务已经提交；这里同样结束读事务，对象仍留在缓存里。
        await old_session.commit()
        await _set_round(factory, approved_at=new_at, property_id=102, amount=499)
        old = BookingService(
            SQLAlchemyApprovalRepository(old_session), SQLAlchemyPermissionChecker(old_session),
            _MatchingHostex(property_id=101, created_at=old_at, rate=399), sensitive,
        )
        if old_exit == "reconcile":
            await old._reconcile_or_mark_review(
                old_approval, attempt=old_at, request_id="REQ-OLD"
            )
        else:
            await old._mark_needs_review(
                old_approval, attempt=old_at, failure_message="旧一轮失败", request_id="REQ-OLD"
            )

    async with factory() as session:
        current = await session.get(BookingApproval, 1)
        assert current.status is ApprovalStatus.CREATING
        assert (current.property_id, current.final_rate_amount) == (102, 499)
        assert (current.hostex_request_id, current.hostex_reservation_code) == (None, None)
        assert current.failure_message is None
        assert current.approved_at.replace(tzinfo=UTC) == new_at

    async with factory() as new_session:
        new_approval = await new_session.get(BookingApproval, 1)
        await new_session.commit()
        await BookingService(
            SQLAlchemyApprovalRepository(new_session), SQLAlchemyPermissionChecker(new_session),
            _MatchingHostex(property_id=102, created_at=new_at, rate=499), sensitive,
        )._reconcile_or_mark_review(new_approval, attempt=new_at, request_id="REQ-NEW")

    async with factory() as session:
        booked = await session.get(BookingApproval, 1)
        assert booked.status is ApprovalStatus.BOOKED
        assert (booked.hostex_reservation_code, booked.hostex_request_id) == (
            "HX-ROOM-102", "REQ-NEW",
        )


def test_an_eleven_digit_number_starting_with_86_keeps_its_prefix() -> None:
    """AR9：只有去掉国家码后剩 11 位才算带区号；本身 11 位、以 86 开头的号码保持原值，
    否则会被截成 9 位，与别的号码误配或漏配。"""
    from homestay_bot.services.approval_page_service import _normalize_phone

    assert _normalize_phone("86013800138") == "86013800138"
    assert _normalize_phone("+86 138-0013-8000") == "13800138000"
    assert _normalize_phone("008613800138000") == "13800138000"
