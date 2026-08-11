import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from typing import Any, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

HOSTEX_BASE_URL = "https://api.myhostex.com/v3"
HOSTEX_SUCCESS_CODES = {0, 200}
TRANSIENT_ERROR_CODES = {429, 500, 502, 503, 504}


class HostexBusinessError(RuntimeError):
    """表示百居易在 HTTP 200 信封中返回的业务错误。"""

    def __init__(
        self,
        error_code: int,
        request_id: str,
        message: str,
        retry_after: float | None = None,
    ) -> None:
        """保留稳定错误码和请求编号，供重试与审计使用。"""
        super().__init__(f"Hostex error {error_code}: {message}")
        self.error_code = error_code
        self.request_id = request_id
        self.retry_after = retry_after


class HostexTransportError(RuntimeError):
    """表示尚未得到明确业务结果的网络或协议错误。"""


class HostexModel(BaseModel):
    """允许百居易后续新增字段，同时严格校验当前使用字段。"""

    model_config = ConfigDict(extra="ignore")


class Channel(HostexModel):
    """表示物理房间或房型关联的渠道房源。"""

    channel_type: str
    listing_id: str
    currency: str | None = None


class Property(HostexModel):
    """表示百居易中的一间物理房间。"""

    id: int
    title: str
    channels: list[Channel] = Field(default_factory=list)
    address: str | None = None


class RoomTypeProperty(HostexModel):
    """表示房型库存池中的物理房间摘要。"""

    id: int
    title: str


class RoomType(HostexModel):
    """表示百居易房型及其物理房间。"""

    id: int
    title: str
    properties: list[RoomTypeProperty]
    channels: list[Channel] = Field(default_factory=list)


class AvailabilityDay(HostexModel):
    """表示物理房间某一天的可用状态。"""

    date: date
    available: bool
    remarks: str = ""


class PropertyAvailability(HostexModel):
    """表示一间物理房间在日期范围内的房态。"""

    property_id: int
    days: list[AvailabilityDay]


class ListingCalendarDay(HostexModel):
    """表示一个渠道房源某天的参考价格和库存。"""

    listing_id: str
    channel_type: str
    date: date
    price: float
    inventory: int
    restrictions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("restrictions", mode="before")
    @classmethod
    def normalize_optional_restrictions(cls, value: Any) -> Any:
        """把文档允许省略且真实接口可能返回的 null 统一为空字典。"""
        return {} if value is None else value


class Reservation(HostexModel):
    """表示用于订单查询和不确定写入核验的关键字段。"""

    reservation_code: str
    stay_code: str
    property_id: int
    check_in_date: date
    check_out_date: date
    status: str
    guest_name: str | None = None
    guest_phone: str | None = None
    created_at: str
    rates: dict[str, Any] = Field(default_factory=dict)


class ReservationQuery(HostexModel):
    """定义第一期订单查询可用的安全过滤条件。"""

    reservation_code: str | None = None
    property_id: int | None = None
    status: str | None = None
    start_check_in_date: date | None = None
    end_check_in_date: date | None = None
    start_check_out_date: date | None = None
    end_check_out_date: date | None = None
    order_by: str = "created_at"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class IncomeMethod(HostexModel):
    """表示百居易可选的收入方式。"""

    id: int
    name: str


class CreateReservationRequest(HostexModel):
    """定义百居易创建直订订单所需的完整字段。"""

    property_id: int
    custom_channel_id: int
    check_in_date: date
    check_out_date: date
    number_of_guests: int
    guest_name: str
    email: str | None = None
    mobile: str
    currency: str = "CNY"
    rate_amount: int
    commission_amount: int = 0
    received_amount: int
    income_method_id: int
    remarks: str | None = None


class CreateReservationResult(HostexModel):
    """保留创建请求编号；订单编号需要写后查询核验。"""

    request_id: str


class HostexClient:
    """封装百居易 OpenAPI，并统一处理信封响应和只读重试。"""

    def __init__(
        self,
        access_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        """创建不泄露 Token 的异步 API 客户端。"""
        self._sleeper = sleeper
        self._client = httpx.AsyncClient(
            base_url=HOSTEX_BASE_URL,
            headers={
                "Hostex-Access-Token": access_token.strip(),
                "User-Agent": "WuhanHomestayBot/0.1 (local-integration)",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        """允许调用方通过异步上下文统一关闭连接。"""
        return self

    async def __aexit__(self, *_: object) -> None:
        """退出异步上下文时释放 HTTP 连接。"""
        await self.aclose()

    async def aclose(self) -> None:
        """显式释放底层 HTTP 连接池。"""
        await self._client.aclose()

    @property
    def is_closed(self) -> bool:
        """公开只读关闭状态，供候选测试和生命周期验收检查资源释放。"""
        return self._client.is_closed

    async def probe_read_only(self) -> None:
        """候选配置只读取房源列表，不触发订单、房态或任何写操作。"""
        await self.list_properties(retry_safe=False)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry_safe: bool = True,
    ) -> dict[str, Any]:
        """发送请求并按百居易业务错误码实施有限重试。"""
        max_attempts = 3 if retry_safe else 1

        for attempt in range(max_attempts):
            try:
                response = await self._client.request(method, path, params=params, json=json)
                response.raise_for_status()
                envelope: dict[str, Any] = response.json()
            except (httpx.HTTPError, ValueError) as error:
                if attempt + 1 >= max_attempts:
                    raise HostexTransportError("百居易请求未得到有效响应") from error
                await self._sleeper(float(2**attempt))
                continue

            error_code = int(envelope.get("error_code", -1))
            if error_code in HOSTEX_SUCCESS_CODES:
                return envelope

            retry_after_header = response.headers.get("Retry-After")
            retry_after = float(retry_after_header) if retry_after_header is not None else None
            business_error = HostexBusinessError(
                error_code=error_code,
                request_id=str(envelope.get("request_id", "")),
                message=str(envelope.get("error_msg", "")),
                retry_after=retry_after,
            )
            if retry_safe and error_code in TRANSIENT_ERROR_CODES and attempt + 1 < max_attempts:
                await self._sleeper(retry_after or float(2**attempt))
                continue
            raise business_error

        raise HostexTransportError("百居易请求超过最大尝试次数")

    async def list_properties(self, *, retry_safe: bool = True) -> list[Property]:
        """读取物理房间，供房型映射、房态查询和员工选房使用。"""
        envelope = await self._request(
            "GET",
            "/properties",
            params={"offset": 0, "limit": 100},
            retry_safe=retry_safe,
        )
        raw_properties = envelope["data"]["properties"]
        return [Property.model_validate(item) for item in raw_properties]

    async def list_availabilities(
        self,
        property_ids: Sequence[int],
        start_date: date | str,
        end_date: date | str,
    ) -> list[PropertyAvailability]:
        """读取主日历房态，不把渠道库存误当成物理房间房态。"""
        if not property_ids:
            return []
        envelope = await self._request(
            "GET",
            "/availabilities",
            params={
                "property_ids": ",".join(str(item) for item in property_ids),
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
        )
        result: list[PropertyAvailability] = []
        for item in envelope["data"]["properties"]:
            result.append(
                PropertyAvailability(
                    property_id=item["id"],
                    days=[AvailabilityDay.model_validate(day) for day in item["availabilities"]],
                )
            )
        return result

    async def list_room_types(self) -> list[RoomType]:
        """读取房型及其关联物理房间。"""
        envelope = await self._request("GET", "/room_types", params={"offset": 0, "limit": 100})
        raw_room_types = envelope["data"]["room_types"]
        return [RoomType.model_validate(item) for item in raw_room_types]

    async def list_reference_prices(
        self,
        start_date: date | str,
        end_date: date | str,
    ) -> list[ListingCalendarDay]:
        """优先读取直订网站渠道价格，并明确仅作为参考价。"""
        properties = await self.list_properties()
        channels = [channel for item in properties for channel in item.channels]
        booking_site_channels = [
            channel for channel in channels if channel.channel_type == "booking_site"
        ]
        selected_channels = booking_site_channels or channels
        if not selected_channels:
            return []

        envelope = await self._request(
            "POST",
            "/listings/calendar",
            json={
                "start_date": str(start_date),
                "end_date": str(end_date),
                "listings": [
                    {
                        "channel_type": channel.channel_type,
                        "listing_id": channel.listing_id,
                    }
                    for channel in selected_channels
                ],
            },
        )
        result: list[ListingCalendarDay] = []
        for listing in envelope["data"]["listings"]:
            for day in listing["calendar"]:
                result.append(
                    ListingCalendarDay(
                        listing_id=listing["listing_id"],
                        channel_type=listing["channel_type"],
                        **day,
                    )
                )
        return result

    async def list_reservations(self, query: ReservationQuery) -> list[Reservation]:
        """按房间和日期查询订单，并自动读取完整 offset/limit 分页。"""
        params = query.model_dump(mode="json", exclude_none=True)
        page_size = int(params["limit"])
        offset = int(params.get("offset", 0))
        reservations: list[Reservation] = []
        seen_stays: set[tuple[str, str]] = set()
        # 7 间房的正常窗口远低于该上限；异常情况下显式失败，禁止静默漏单。
        for _page_number in range(100):
            params["offset"] = offset
            envelope = await self._request("GET", "/reservations", params=params)
            raw_items = envelope["data"].get("reservations", [])
            page = [Reservation.model_validate(item) for item in raw_items]
            new_items = [
                item
                for item in page
                if (item.reservation_code, item.stay_code) not in seen_stays
            ]
            if page and not new_items and len(page) >= page_size:
                raise HostexTransportError("百居易订单分页返回重复页面")
            for item in new_items:
                seen_stays.add((item.reservation_code, item.stay_code))
            reservations.extend(new_items)
            if len(page) < page_size:
                return reservations
            offset += len(page)
        raise HostexTransportError("百居易订单分页超过安全上限")

    async def list_income_methods(self) -> list[IncomeMethod]:
        """读取员工审批订单时可选的百居易收款方式。"""
        envelope = await self._request("GET", "/income_methods")
        return [IncomeMethod.model_validate(item) for item in envelope["data"]["income_methods"]]

    async def create_reservation(
        self, request: CreateReservationRequest
    ) -> CreateReservationResult:
        """创建直订订单；任何网络失败都不得在本层自动重放。"""
        envelope = await self._request(
            "POST",
            "/reservations",
            json=request.model_dump(mode="json", exclude_none=True),
            retry_safe=False,
        )
        return CreateReservationResult(request_id=str(envelope["request_id"]))
