from datetime import date

from pydantic import BaseModel, Field


class ConfirmBookingCommand(BaseModel):
    """定义员工确认订单时必须提交的业务字段。"""

    property_id: int
    final_rate_amount: int = Field(gt=0)
    received_amount: int = Field(ge=0)
    income_method_id: int
    payment_confirmed: bool


class BookingRequest(BaseModel):
    """定义机器人收集并交给审批流程的预订资料。"""

    check_in_date: date
    check_out_date: date
    number_of_guests: int = Field(gt=0)
    guest_name: str = Field(min_length=1, max_length=100)
    guest_mobile: str = Field(min_length=6, max_length=32)
    room_type_preference: str = Field(min_length=1, max_length=128)
    special_requests: str | None = Field(default=None, max_length=1000)
