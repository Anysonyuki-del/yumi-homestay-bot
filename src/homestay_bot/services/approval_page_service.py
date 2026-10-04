import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import AuditLog, BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import ReservationQuery, reference_price_currency
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.booking_service import BookingService

logger = logging.getLogger(__name__)


class ApprovalHostexPort(Protocol):
    """定义审批详情页读取百居易字典和参考数据的接口。"""

    async def list_properties(self) -> Sequence[Any]:
        """返回可供员工最终选择的物理房间。"""

    async def list_reference_prices(
        self, start_date: date | str, end_date: date | str
    ) -> Sequence[Any]:
        """返回入住区间的渠道日历参考价。"""

    async def list_income_methods(self) -> Sequence[Any]:
        """返回百居易账户可用的收入方式。"""

    async def list_reservations(self, query: ReservationQuery) -> Sequence[Any]:
        """只读查询订单；人工核验后回到待审批前用来防重复下单。"""


@dataclass(frozen=True)
class ApprovalPageView:
    """仅暴露审批模板需要的只读字段，避免 ORM 密文进入视图层。"""

    id: int
    approval_code: str
    status: ApprovalStatus
    check_in_date: date
    check_out_date: date
    number_of_guests: int
    guest_name: str
    room_type_preference: str
    special_requests: str | None


_REJECTABLE_STATUSES = frozenset(
    {
        ApprovalStatus.PENDING,
        ApprovalStatus.NEEDS_REVIEW,
        ApprovalStatus.CONFLICT,
    }
)


class ApprovalActionRefused(OperationRefused):
    """审批人工核验动作被拒；文案写给管理员看，页面回到审批详情并显示原因。"""


# 百居易订单号形如字母数字加连字符；限制字符集，避免把整段说明或网址当订单号存下。
_RESERVATION_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# 回到待审批时清空的上次确认字段：防止拿旧金额、旧房间直接重复下单（Spec R6）。
_CONFIRMATION_FIELDS = (
    "property_id",
    "final_rate_amount",
    "received_amount",
    "income_method_id",
    "approved_by",
    "approved_at",
    "hostex_request_id",
    "failure_code",
    "failure_message",
)
# 同一入住日的订单达到这个数就按查不清处理。百居易客户端会自动读完全部分页，这只是
# 新增的保守阈值，不是客户端截断。
_RESERVATION_LOOKUP_LIMIT = 100
_PHONE_PREFIXES = ("0086", "86")


def _attempt_fingerprint(approval: BookingApproval) -> tuple[object, ...]:
    """标识「这一轮确认」：确认时间、房间与上游请求编号，任何一项变了就是新一轮。"""
    return (approval.approved_at, approval.property_id, approval.hostex_request_id)


def _normalize_name(value: str | None) -> str:
    """姓名去掉全部空白、不分大小写后比较。"""
    return re.sub(r"\s+", "", value or "").casefold()


def _normalize_phone(value: str | None) -> str:
    """手机只比数字，并去掉 +86、86、0086 国家码前缀（剩余 11 位时）。"""
    digits = re.sub(r"\D", "", value or "")
    for prefix in _PHONE_PREFIXES:
        if digits.startswith(prefix) and len(digits) - len(prefix) == 11:
            return digits[len(prefix):]
    return digits


def _maybe_same_guest(reservation: Any, guest_name: str, guest_mobile: str) -> bool:
    """同日期订单是否可能就是这位客人（用户确认的 AR2 规则，2026-10-01）。

    姓名一致、手机一致、或订单姓名与手机都缺失（无法排除），任一成立即算疑似。
    宁可同日同名不同人也被挡住、由管理员改用填入订单号或拒绝，也不冒重复下单的险。
    """
    name = _normalize_name(getattr(reservation, "guest_name", None))
    phone = _normalize_phone(getattr(reservation, "guest_phone", None))
    if not name and not phone:
        return True
    if name and name == _normalize_name(guest_name):
        return True
    return bool(phone) and phone == _normalize_phone(guest_mobile)


class ApprovalPageService:
    """汇总审批详情，并把确认动作交给安全下单状态机。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        hostex: ApprovalHostexPort,
        booking: BookingService,
        sensitive_data: ApprovalSensitiveData,
    ) -> None:
        """注入当前会话、百居易只读接口、下单与敏感数据服务。"""
        self._session = session
        self._hostex = hostex
        self._booking = booking
        self._sensitive_data = sensitive_data

    # 只有仍可能落单的审批才需要下单参考数据；已结束的审批不必为看一眼历史
    # 就去请求外部接口。
    _NEEDS_REFERENCE_DATA = frozenset(
        {
            ApprovalStatus.PENDING,
            ApprovalStatus.CREATING,
            ApprovalStatus.NEEDS_REVIEW,
            ApprovalStatus.CONFLICT,
        }
    )

    async def _reference(
        self,
        label: str,
        call: Callable[[], Awaitable[Sequence[Any]]],
        unavailable: list[str],
    ) -> list[Any]:
        """取一项参考数据；失败只记下这一项，不牵连其余。

        逐项降级而不是整体作废：三项里挂一项，其余两项仍然有用，员工也还能看
        本地审批并合法拒绝。失败原因不回显给页面——外部异常文本可能带上游地址
        或请求细节。
        """
        try:
            return [item.model_dump(mode="json") for item in await call()]
        except Exception:
            logger.warning(
                "审批参考数据不可用 item=%s error_type=%s",
                label,
                "upstream_error",
            )
            unavailable.append(label)
            return []

    @staticmethod
    def _price_groups(
        properties: list[Any], prices: list[Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按完整渠道身份归属并统一币种；直订默认来源明确标注，未知房间单列核对。"""
        groups = {item["id"]: {"id": item["id"], "title": item["title"], "prices": []}
                  for item in properties}
        owners: dict[tuple[str, str], set[int]] = {}
        currencies: dict[tuple[str, str], tuple[str, str | None]] = {}
        for item in properties:
            for channel in item.get("channels", []):
                key = (channel["channel_type"], channel["listing_id"])
                owners.setdefault(key, set()).add(item["id"])
                raw_currency = channel.get("currency")
                currency = reference_price_currency(channel["channel_type"], raw_currency)
                currencies[key] = (
                    "¥" if currency == "CNY" else currency or "币种未确认",
                    "直订默认人民币" if currency == "CNY" and raw_currency is None else None,
                )
        unmatched = []
        for row in prices:
            key = (row["channel_type"], row["listing_id"])
            room_ids = owners.get(key, set())
            currency_label, currency_note = currencies.get(key, ("币种未确认", None))
            display_row = {**row, "currency_label": currency_label, "currency_note": currency_note}
            if len(room_ids) == 1:
                groups[next(iter(room_ids))]["prices"].append(display_row)
            else:
                unmatched.append(display_row)
        return list(groups.values()), unmatched

    async def get_detail(self, approval_id: int) -> dict[str, Any]:
        """读取审批单，并按需取小规模参考数据；上游失败时逐项降级。"""
        approval = await self._session.get(BookingApproval, approval_id)
        if approval is None:
            raise LookupError(f"审批单不存在: {approval_id}")
        # ponytail: 审计目标暂无索引；当前详情访问量低，实际出现慢查询时再补目标索引。
        discarded = list(await self._session.scalars(
            select(AuditLog).where(
                AuditLog.action == "booking_approval_late_result_discarded",
                AuditLog.target_type == "booking_approval",
                AuditLog.target_id == str(approval_id),
            ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(10)
        ))
        unavailable: list[str] = []
        needs_reference = approval.status in self._NEEDS_REFERENCE_DATA
        if needs_reference:
            properties = await self._reference(
                "房源字典", self._hostex.list_properties, unavailable
            )
            prices = await self._reference(
                "渠道日历参考价",
                lambda: self._hostex.list_reference_prices(
                    approval.check_in_date, approval.check_out_date
                ),
                unavailable,
            )
            income_methods = await self._reference(
                "收入方式", self._hostex.list_income_methods, unavailable
            )
        else:
            properties, prices, income_methods = [], [], []
        sensitive = self._sensitive_data.read(approval)
        groups, unmatched = self._price_groups(properties, prices)
        return {
            # 下单依赖实时房态与价格：缺任何一项都不许确认，绝不拿旧数据兜底。
            "can_confirm": needs_reference and not unavailable,
            "reference_unavailable": unavailable,
            "approval": self._to_view(approval),
            # 模板只接收提醒必需字段，不透传审计 details 中其他可能敏感的内容。
            "late_results": [{
                "created_at": row.created_at,
                "request_id": row.details.get("request_id"),
                "reservation_codes": row.details.get("reservation_codes", []),
                "error_code": row.details.get("error_code"),
            } for row in discarded],
            # 1.41.0 起后台显示完整手机号（用户决定可记录客人信息，只有登录员工可见）；
            # 模板变量沿用原名，已清除的旧记录仍显示「已清理」。
            "masked_mobile": (
                sensitive.guest_mobile if sensitive.guest_mobile is not None else "已清理"
            ),
            "properties": properties,
            "reference_prices": prices,
            "reference_price_groups": groups,
            "unmatched_reference_prices": unmatched,
            "income_methods": income_methods,
        }

    async def list_pending(
        self, *, offset: int, limit: int
    ) -> list[ApprovalPageView]:
        """按稳定顺序分页返回需要员工关注的审批单。"""
        statement = (
            select(BookingApproval)
            .where(
                BookingApproval.status.in_(
                    {
                        ApprovalStatus.PENDING,
                        ApprovalStatus.CREATING,
                        ApprovalStatus.NEEDS_REVIEW,
                        ApprovalStatus.CONFLICT,
                    }
                )
            )
            .order_by(BookingApproval.created_at.desc(), BookingApproval.id.desc())
            .offset(offset)
            .limit(limit)
        )
        approvals = list((await self._session.scalars(statement)).all())
        return [self._to_view(approval) for approval in approvals]

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """把一次性表单确认交给具备幂等保护的下单服务。"""
        return await self._booking.confirm_and_create(
            approval_id, employee_id, command
        )

    async def reject(
        self,
        approval_id: int,
        employee_id: int,
        reason: str,
    ) -> BookingApproval:
        """把待处理审批标记为已拒绝，并把拒绝原因写入审计。

        ApprovalStatus.REJECTED 此前没有任何写入点，审批只能确认不能拒绝。
        原因必须在拒绝当下记入审计：数据保留逻辑会清理已拒绝审批，之后无从追溯。
        """
        cleaned = reason.strip()
        if not cleaned:
            raise ValueError("拒绝原因不能为空")
        approval = await self._session.scalar(
            select(BookingApproval)
            .where(BookingApproval.id == approval_id)
            .with_for_update()
        )
        if approval is None:
            raise LookupError("审批单不存在")
        if approval.status not in _REJECTABLE_STATUSES:
            raise ValueError("当前审批状态不能拒绝")
        previous = approval.status
        approval.status = ApprovalStatus.REJECTED
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action="booking_approval_rejected",
                target_type="booking_approval",
                target_id=str(approval_id),
                details={
                    "from_status": previous.value,
                    "reason": cleaned[:500],
                },
            )
        )
        await self._session.flush()
        return approval

    async def backfill_reservation(
        self, approval_id: int, employee_id: int, reservation_code: str
    ) -> BookingApproval:
        """需复核的审批在百居易里确有订单：管理员填入订单号，直接标为已预订（A1）。

        按用户决定（2026-10-01「1.直接标」）不再向百居易核对；页面要求从百居易后台
        复制订单号并在确认框里重复显示。订单号有唯一约束，先查重给出可读提示，
        并发时由约束兜底，同样转成提示而不是 500。
        """
        code = reservation_code.strip()
        if not _RESERVATION_CODE.fullmatch(code):
            raise ApprovalActionRefused(
                "订单号格式不对：请从百居易后台复制订单号，只含字母、数字、连字符或下划线",
                status_code=422,
                return_to=self._detail_path(approval_id),
            )
        approval = await self._lock(approval_id)
        self._require_status(approval, ApprovalStatus.NEEDS_REVIEW, "填入订单号")
        duplicate = await self._session.scalar(
            select(BookingApproval.id).where(
                BookingApproval.hostex_reservation_code == code,
                BookingApproval.id != approval_id,
            )
        )
        if duplicate is not None:
            raise self._duplicate_code(approval_id)
        previous = approval.status
        # 状态、订单号与审计在同一次 flush 里写入，一起提交或一起回滚（Spec §6 AR5）。
        # 单次 flush 更简单，状态、订单号与审计共用外层事务，不依赖保存点语义。
        # 唯一键冲突时整个会话由当前调用方回滚。
        approval.status = ApprovalStatus.BOOKED
        approval.hostex_reservation_code = code
        self._audit(
            employee_id,
            "booking_approval_backfilled",
            approval_id,
            {"from_status": previous.value, "reservation_code": code},
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise self._duplicate_code(approval_id) from error
        return approval

    async def reopen_after_review(self, approval_id: int, employee_id: int) -> BookingApproval:
        """需复核但百居易确实没建成：先反查防重，再回到待审批重新确认（A2）。

        反查放在加锁之前：不持有行锁去等外部接口。查完加锁后重新校验状态，期间被
        别人处理过就拒绝。查到入住、退房日期与姓名、手机都一致的订单（不论订单
        状态），或查询失败、结果取满，都不回退——宁可让管理员改用填入订单号或拒绝，
        也不冒重复下单的险（Spec R3）。
        """
        approval = await self._session.get(BookingApproval, approval_id)
        if approval is None:
            raise LookupError(f"审批单不存在: {approval_id}")
        self._require_status(approval, ApprovalStatus.NEEDS_REVIEW, "回到待审批")
        # 这一轮确认的指纹：查询期间若别人已回退、重新确认并再次进入需复核，状态
        # 仍是 NEEDS_REVIEW，只看状态会把新一轮的结果退回去、造成第二次建单（Codex AR1）。
        # 每次确认都会写入新的 approved_at，现有字段足以区分轮次。
        attempt = _attempt_fingerprint(approval)
        try:
            sensitive = self._sensitive_data.require_for_booking(approval)
        except ValueError as error:
            raise ApprovalActionRefused(
                "客人资料已按保留期清理，无法反查是否已有订单，不能回到待审批；请拒绝后让客人重新申请",
                return_to=self._detail_path(approval_id),
            ) from error
        try:
            candidates = list(
                await self._hostex.list_reservations(
                    ReservationQuery(
                        property_id=approval.property_id,
                        start_check_in_date=approval.check_in_date,
                        end_check_in_date=approval.check_in_date,
                        limit=_RESERVATION_LOOKUP_LIMIT,
                    )
                )
            )
        except Exception as error:
            logger.warning(
                "审批回退前反查失败 approval_id=%s error_type=%s",
                approval_id,
                type(error).__name__,
            )
            raise ApprovalActionRefused(
                "暂时查不到百居易订单，无法确认没有重复，请稍后再试",
                return_to=self._detail_path(approval_id),
            ) from error
        if len(candidates) >= _RESERVATION_LOOKUP_LIMIT:
            raise ApprovalActionRefused(
                "同一入住日的订单太多，无法确认没有重复；请到百居易后台核对后填入订单号或拒绝",
                return_to=self._detail_path(approval_id),
            )
        matches = [
            item
            for item in candidates
            if item.check_in_date == approval.check_in_date
            and item.check_out_date == approval.check_out_date
            and _maybe_same_guest(item, sensitive.guest_name, sensitive.guest_mobile)
        ]
        if matches:
            codes = "、".join(str(item.reservation_code) for item in matches[:3])
            raise ApprovalActionRefused(
                f"百居易里已有同一客人、同一日期的订单（{codes}），不能回到待审批；"
                "确认是这一单的话请改用「填入订单号」",
                return_to=self._detail_path(approval_id),
            )
        locked = await self._lock(approval_id)
        self._require_status(locked, ApprovalStatus.NEEDS_REVIEW, "回到待审批")
        if _attempt_fingerprint(locked) != attempt:
            raise ApprovalActionRefused(
                "查询期间这张审批已被重新确认过，刚才的核对结果已经过期，请刷新页面后重新判断",
                return_to=self._detail_path(approval_id),
            )
        self._reset_to_pending(locked)
        self._audit(
            employee_id,
            "booking_approval_reopened",
            approval_id,
            {
                "from_status": ApprovalStatus.NEEDS_REVIEW.value,
                "checked_reservations": len(candidates),
            },
        )
        await self._session.flush()
        return locked

    async def recheck_after_conflict(self, approval_id: int, employee_id: int) -> BookingApproval:
        """有冲突的审批回到待审批（A3）。这里不调用百居易：下次确认时照常实时查房态，
        仍不满足会再次成为有冲突。"""
        approval = await self._lock(approval_id)
        self._require_status(approval, ApprovalStatus.CONFLICT, "回到待审批")
        self._reset_to_pending(approval)
        self._audit(
            employee_id,
            "booking_approval_recheck",
            approval_id,
            {"from_status": ApprovalStatus.CONFLICT.value},
        )
        await self._session.flush()
        return approval

    async def _lock(self, approval_id: int) -> BookingApproval:
        """行锁读取审批单，两位管理员同时操作时串行（Spec R5）。"""
        approval = await self._session.scalar(
            select(BookingApproval)
            .where(BookingApproval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if approval is None:
            raise LookupError(f"审批单不存在: {approval_id}")
        return approval

    def _require_status(
        self, approval: BookingApproval, expected: ApprovalStatus, action: str
    ) -> None:
        """状态已被别人改过时拒绝，并告诉管理员现在是什么状态。"""
        if approval.status is not expected:
            raise ApprovalActionRefused(
                f"这张审批当前不能{action}：状态已经变化，请刷新页面查看最新状态",
                return_to=self._detail_path(approval.id),
            )

    @staticmethod
    def _reset_to_pending(approval: BookingApproval) -> None:
        """回到待审批并清空上次确认填写的字段，由管理员重新填写确认。"""
        approval.status = ApprovalStatus.PENDING
        for field in _CONFIRMATION_FIELDS:
            setattr(approval, field, None)

    def _audit(
        self, employee_id: int, action: str, approval_id: int, details: dict[str, Any]
    ) -> None:
        """审计只记动作、状态与订单号，不记客人信息。"""
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="booking_approval",
                target_id=str(approval_id),
                details=details,
            )
        )

    def _duplicate_code(self, approval_id: int) -> ApprovalActionRefused:
        """订单号已登记在另一张审批上。"""
        return ApprovalActionRefused(
            "这个订单号已经登记在另一张审批上，请核对后再填",
            return_to=self._detail_path(approval_id),
        )

    @staticmethod
    def _detail_path(approval_id: int) -> str:
        """操作被拒后回到的审批详情页。"""
        return f"/employee/approvals/{approval_id}"

    def _to_view(self, approval: BookingApproval) -> ApprovalPageView:
        """解密模板所需字段并复制到不可变视图，禁止泄露 ORM 密文字段。"""
        sensitive = self._sensitive_data.read(approval)
        return ApprovalPageView(
            id=approval.id,
            approval_code=approval.approval_code,
            status=approval.status,
            check_in_date=approval.check_in_date,
            check_out_date=approval.check_out_date,
            number_of_guests=approval.number_of_guests,
            guest_name=sensitive.guest_name or "已清理",
            room_type_preference=approval.room_type_preference,
            special_requests=sensitive.special_requests,
        )
