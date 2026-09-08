"""审批详情在百居易参考数据不可用时仍须可看、可拒。"""

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.services.approval_page_service import ApprovalPageService


class _Approval:
    """最小审批单，只带页面与降级判定用得到的字段。"""

    def __init__(self, status: ApprovalStatus) -> None:
        """构造一条指定状态的审批单。"""
        self.id = 1
        self.approval_code = "AP-1"
        self.status = status
        self.guest_name = "客人"
        self.check_in_date = date(2026, 9, 10)
        self.check_out_date = date(2026, 9, 12)
        self.number_of_guests = 2
        self.room_type_preference = "大床房"
        self.special_requests = None
        self.created_at = None


class _Session:
    """只实现 get 的会话替身。"""

    def __init__(self, approval: object) -> None:
        """保存要返回的审批单。"""
        self._approval = approval

    async def get(self, model: object, approval_id: int) -> object:
        """按主键返回预置审批单。"""
        return self._approval


class _Hostex:
    """可分别注入三个接口失败的百居易替身。"""

    def __init__(self, failing: set[str] | None = None) -> None:
        """记录哪些接口应当抛错，并统计调用。"""
        self.failing = failing or set()
        self.calls: list[str] = []

    async def list_properties(self) -> list[Any]:
        """房源字典。"""
        self.calls.append("properties")
        if "properties" in self.failing:
            raise TimeoutError("上游超时")
        return []

    async def list_reference_prices(self, start_date, end_date) -> list[Any]:
        """区间参考价。"""
        self.calls.append("prices")
        if "prices" in self.failing:
            raise TimeoutError("上游超时")
        return []

    async def list_income_methods(self) -> list[Any]:
        """收入方式。"""
        self.calls.append("income")
        if "income" in self.failing:
            raise TimeoutError("上游超时")
        return []


class _Sensitive:
    """返回固定脱敏来源。"""

    def read(self, approval: object) -> SimpleNamespace:
        """返回带手机号的敏感数据。"""
        return SimpleNamespace(
            guest_mobile="13800138000",
            guest_name="客人",
            special_requests=None,
        )


def _service(hostex: _Hostex, approval: object) -> ApprovalPageService:
    """按依赖构造被测服务。"""
    return ApprovalPageService(
        session=_Session(approval),
        hostex=hostex,
        booking=SimpleNamespace(),
        sensitive_data=_Sensitive(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["properties", "prices", "income"])
async def test_detail_survives_any_single_reference_failure(broken: str) -> None:
    """三个参考接口各自失败时，本地审批仍要能看，且只禁用确认下单。

    此前 get_detail 无条件顺序 await 三个接口且无降级，上游一挂，员工连已有的
    审批基本信息和本地拒绝入口都打不开——查看历史审批也白白承担外部依赖。
    """
    approval = _Approval(ApprovalStatus.PENDING)
    service = _service(_Hostex({broken}), approval)

    detail = await service.get_detail(1)

    assert detail["approval"].approval_code == "AP-1"
    assert detail["masked_mobile"] == "138****8000"
    # 只有失败的那一项被标记，不因为一个挂了就整体作废。
    assert len(detail["reference_unavailable"]) == 1
    # 下单依赖实时房态与价格，缺任何一项都不许确认；绝不用旧数据兜底放行。
    assert detail["can_confirm"] is False


@pytest.mark.asyncio
async def test_detail_allows_confirmation_when_all_reference_data_is_present() -> None:
    """参考数据齐备时确认路径不受影响。"""
    service = _service(_Hostex(), _Approval(ApprovalStatus.PENDING))

    detail = await service.get_detail(1)

    assert detail["reference_unavailable"] == []
    assert detail["can_confirm"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ApprovalStatus.BOOKED, ApprovalStatus.REJECTED])
async def test_finished_approvals_do_not_touch_hostex_at_all(
    status: ApprovalStatus,
) -> None:
    """已结束的审批不需要下单参考数据，不该再请求外部接口。"""
    hostex = _Hostex()
    service = _service(hostex, _Approval(status))

    detail = await service.get_detail(1)

    assert hostex.calls == []
    assert detail["can_confirm"] is False
    assert detail["reference_unavailable"] == []
