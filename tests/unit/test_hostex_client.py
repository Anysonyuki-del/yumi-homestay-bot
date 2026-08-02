from collections.abc import Callable

import httpx
import pytest

from homestay_bot.integrations.hostex_client import (
    CreateReservationRequest,
    HostexBusinessError,
    HostexClient,
    HostexTransportError,
    ReservationQuery,
)


def json_transport(
    responder: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    """创建可检查请求内容的 HTTPX 测试传输层。"""
    return httpx.MockTransport(responder)


@pytest.mark.asyncio
async def test_hostex_checks_error_code_even_when_http_is_200() -> None:
    """百居易业务错误必须由响应体判断，不能只看 HTTP 状态。"""
    transport = json_transport(
        lambda request: httpx.Response(
            200,
            json={"request_id": "RT-1", "error_code": 422, "error_msg": "invalid date"},
        )
    )
    client = HostexClient("secret", transport=transport)

    with pytest.raises(HostexBusinessError) as error:
        await client.list_availabilities([101], "2026-08-01", "2026-08-02")

    assert error.value.request_id == "RT-1"
    assert error.value.error_code == 422


@pytest.mark.asyncio
async def test_hostex_accepts_200_business_success_code() -> None:
    """百居易真实接口以业务码 200 表示成功时应正常解析数据。"""
    transport = json_transport(
        lambda request: httpx.Response(
            200,
            json={
                "request_id": "RT-200",
                "error_code": 200,
                "error_msg": "Done.",
                "data": {"properties": [], "total": 0},
            },
        )
    )
    client = HostexClient("secret", transport=transport)

    try:
        properties = await client.list_properties()
    finally:
        await client.aclose()

    assert properties == []


@pytest.mark.asyncio
async def test_list_availabilities_sends_token_and_parses_days() -> None:
    """房态查询必须使用正确请求头、查询参数并解析每日可用性。"""

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.headers["Hostex-Access-Token"] == "secret"
        assert request.headers["User-Agent"].startswith("WuhanHomestayBot/")
        assert request.url.params["property_ids"] == "101,102"
        assert request.url.params["start_date"] == "2026-08-01"
        assert request.url.params["end_date"] == "2026-08-02"
        return httpx.Response(
            200,
            json={
                "request_id": "RT-2",
                "error_code": 0,
                "error_msg": "",
                "data": {
                    "properties": [
                        {
                            "id": 101,
                            "availabilities": [
                                {
                                    "date": "2026-08-01",
                                    "available": True,
                                    "remarks": "",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))
    result = await client.list_availabilities([101, 102], "2026-08-01", "2026-08-02")

    assert result[0].property_id == 101
    assert result[0].days[0].available is True


@pytest.mark.asyncio
async def test_list_properties_parses_channels() -> None:
    """房间查询应保留后续参考价查询需要的渠道信息。"""
    transport = json_transport(
        lambda request: httpx.Response(
            200,
            json={
                "request_id": "RT-3",
                "error_code": 0,
                "error_msg": "",
                "data": {
                    "total": 1,
                    "properties": [
                        {
                            "id": 101,
                            "title": "江景大床房 101",
                            "channels": [
                                {
                                    "channel_type": "booking_site",
                                    "listing_id": "listing-101",
                                    "currency": "CNY",
                                }
                            ],
                        }
                    ],
                },
            },
        )
    )
    client = HostexClient("secret", transport=transport)

    result = await client.list_properties()

    assert result[0].title == "江景大床房 101"
    assert result[0].channels[0].channel_type == "booking_site"


@pytest.mark.asyncio
async def test_list_room_types_parses_linked_properties() -> None:
    """房型查询应返回库存池内可供员工选择的物理房间。"""
    transport = json_transport(
        lambda request: httpx.Response(
            200,
            json={
                "request_id": "RT-4",
                "error_code": 0,
                "error_msg": "",
                "data": {
                    "total": 1,
                    "room_types": [
                        {
                            "id": 10,
                            "title": "江景房",
                            "properties": [{"id": 101, "title": "江景大床房 101"}],
                            "channels": [],
                        }
                    ],
                },
            },
        )
    )
    client = HostexClient("secret", transport=transport)

    room_types = await client.list_room_types()

    assert room_types[0].properties[0].id == 101


@pytest.mark.asyncio
async def test_reference_prices_prefer_booking_site_channel() -> None:
    """存在直订网站渠道时，参考价不应混入 OTA 渠道价。"""
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/properties"):
            return httpx.Response(
                200,
                json={
                    "request_id": "RT-5",
                    "error_code": 0,
                    "error_msg": "",
                    "data": {
                        "total": 1,
                        "properties": [
                            {
                                "id": 101,
                                "title": "101",
                                "channels": [
                                    {
                                        "channel_type": "airbnb",
                                        "listing_id": "airbnb-101",
                                    },
                                    {
                                        "channel_type": "booking_site",
                                        "listing_id": "direct-101",
                                    },
                                ],
                            }
                        ],
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "request_id": "RT-6",
                "error_code": 0,
                "error_msg": "",
                "data": {
                    "listings": [
                        {
                            "channel_type": "booking_site",
                            "listing_id": "direct-101",
                            "calendar": [
                                {
                                    "date": "2026-08-01",
                                    "price": 399,
                                    "inventory": 1,
                                    "restrictions": None,
                                }
                            ],
                        }
                    ]
                },
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))
    prices = await client.list_reference_prices("2026-08-01", "2026-08-02")

    assert prices[0].price == 399
    assert prices[0].restrictions == {}
    assert requests[1].content.count(b"booking_site") == 1
    assert b"airbnb" not in requests[1].content


@pytest.mark.asyncio
async def test_list_reservations_serializes_date_filters() -> None:
    """订单查询应把日期过滤条件序列化为百居易接受的格式。"""

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.params["property_id"] == "101"
        assert request.url.params["start_check_in_date"] == "2026-08-01"
        return httpx.Response(
            200,
            json={
                "request_id": "RT-7",
                "error_code": 0,
                "error_msg": "",
                "data": {
                    "reservations": [
                        {
                            "reservation_code": "R-1",
                            "stay_code": "S-1",
                            "property_id": 101,
                            "check_in_date": "2026-08-01",
                            "check_out_date": "2026-08-02",
                            "status": "accepted",
                            "created_at": "2026-07-29T00:00:00+08:00",
                        }
                    ]
                },
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))
    reservations = await client.list_reservations(
        ReservationQuery(property_id=101, start_check_in_date="2026-08-01")
    )

    assert reservations[0].reservation_code == "R-1"


@pytest.mark.asyncio
async def test_list_reservations_fetches_all_pages() -> None:
    """订单查询必须翻完 offset/limit 分页，避免对账窗口静默漏单。"""
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        pages = {
            0: [
                {
                    "reservation_code": "R-1",
                    "stay_code": "S-1",
                    "property_id": 101,
                    "check_in_date": "2026-08-01",
                    "check_out_date": "2026-08-02",
                    "status": "accepted",
                    "created_at": "2026-07-29T00:00:00+00:00",
                },
                {
                    "reservation_code": "R-2",
                    "stay_code": "S-2",
                    "property_id": 102,
                    "check_in_date": "2026-08-02",
                    "check_out_date": "2026-08-03",
                    "status": "accepted",
                    "created_at": "2026-07-29T00:00:00+00:00",
                },
            ],
            2: [
                {
                    "reservation_code": "R-3",
                    "stay_code": "S-3",
                    "property_id": 103,
                    "check_in_date": "2026-08-03",
                    "check_out_date": "2026-08-04",
                    "status": "accepted",
                    "created_at": "2026-07-29T00:00:00+00:00",
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "request_id": f"RT-{offset}",
                "error_code": 0,
                "error_msg": "",
                "data": {"reservations": pages[offset]},
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))
    reservations = await client.list_reservations(
        ReservationQuery(limit=2)
    )

    assert [item.reservation_code for item in reservations] == ["R-1", "R-2", "R-3"]
    assert [int(request.url.params["offset"]) for request in requests] == [0, 2]


@pytest.mark.asyncio
async def test_list_reservations_fetches_exactly_forty_five_records() -> None:
    """默认每页二十条时，四十五条订单必须完整读取三页。"""
    requested_offsets: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        """按请求 offset 返回四十五条确定性订单切片。"""
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        requested_offsets.append(offset)
        items = [
            {
                "reservation_code": f"R-{index}",
                "stay_code": f"S-{index}",
                "property_id": 100 + index,
                "check_in_date": "2026-08-01",
                "check_out_date": "2026-08-02",
                "status": "accepted",
                "created_at": "2026-07-29T00:00:00+00:00",
            }
            for index in range(offset, min(offset + limit, 45))
        ]
        return httpx.Response(
            200,
            json={
                "request_id": f"RT-{offset}",
                "error_code": 0,
                "error_msg": "",
                "data": {"reservations": items},
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))
    reservations = await client.list_reservations(ReservationQuery())

    assert len(reservations) == 45
    assert requested_offsets == [0, 20, 40]


@pytest.mark.asyncio
async def test_list_reservations_rejects_repeated_full_page() -> None:
    """百居易重复返回同一满页时必须显式失败，不能无限翻页或静默漏单。"""
    repeated = [
        {
            "reservation_code": "R-1",
            "stay_code": "S-1",
            "property_id": 101,
            "check_in_date": "2026-08-01",
            "check_out_date": "2026-08-02",
            "status": "accepted",
            "created_at": "2026-07-29T00:00:00+00:00",
        }
    ]

    def responder(request: httpx.Request) -> httpx.Response:
        """忽略 offset 并持续返回同一页。"""
        return httpx.Response(
            200,
            json={
                "request_id": "RT-repeat",
                "error_code": 0,
                "error_msg": "",
                "data": {"reservations": repeated},
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))

    with pytest.raises(HostexTransportError, match="重复页面"):
        await client.list_reservations(ReservationQuery(limit=1))


@pytest.mark.asyncio
async def test_list_reservations_rejects_more_than_one_hundred_pages() -> None:
    """持续返回唯一满页超过安全上限时必须失败。"""

    def responder(request: httpx.Request) -> httpx.Response:
        """每个 offset 返回不同订单，使测试只命中页数上限。"""
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "request_id": f"RT-{offset}",
                "error_code": 0,
                "error_msg": "",
                "data": {
                    "reservations": [
                        {
                            "reservation_code": f"R-{offset}",
                            "stay_code": f"S-{offset}",
                            "property_id": 101,
                            "check_in_date": "2026-08-01",
                            "check_out_date": "2026-08-02",
                            "status": "accepted",
                            "created_at": "2026-07-29T00:00:00+00:00",
                        }
                    ]
                },
            },
        )

    client = HostexClient("secret", transport=json_transport(responder))

    with pytest.raises(HostexTransportError, match="安全上限"):
        await client.list_reservations(ReservationQuery(limit=1))


@pytest.mark.asyncio
async def test_list_income_methods_parses_dictionary() -> None:
    """审批页应能读取百居易账户真实可用的收款方式。"""
    transport = json_transport(
        lambda request: httpx.Response(
            200,
            json={
                "request_id": "RT-8",
                "error_code": 0,
                "error_msg": "",
                "data": {"income_methods": [{"id": 1, "name": "微信支付"}]},
            },
        )
    )
    client = HostexClient("secret", transport=transport)

    methods = await client.list_income_methods()

    assert methods[0].name == "微信支付"


@pytest.mark.asyncio
async def test_read_request_retries_429_using_retry_after() -> None:
    """只读请求命中限流时应按 Retry-After 退避后重试。"""
    attempts = 0
    delays: list[float] = []

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                headers={"Retry-After": "2"},
                json={
                    "request_id": "RT-9",
                    "error_code": 429,
                    "error_msg": "Too Many Attempts",
                },
            )
        return httpx.Response(
            200,
            json={
                "request_id": "RT-10",
                "error_code": 0,
                "error_msg": "",
                "data": {"total": 0, "properties": []},
            },
        )

    async def record_delay(delay: float) -> None:
        """记录退避时间，避免单元测试真实等待。"""
        delays.append(delay)

    client = HostexClient("secret", transport=json_transport(responder), sleeper=record_delay)
    properties = await client.list_properties()

    assert properties == []
    assert attempts == 2
    assert delays == [2.0]


@pytest.mark.asyncio
async def test_create_reservation_sends_required_fields_without_retry() -> None:
    """直订写入必须发送完整字段，网络失败时不得自动重放。"""
    attempts = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timeout", request=request)

    client = HostexClient("secret", transport=json_transport(responder))
    request = CreateReservationRequest(
        property_id=101,
        custom_channel_id=1,
        check_in_date="2026-08-01",
        check_out_date="2026-08-02",
        number_of_guests=2,
        guest_name="张三",
        mobile="13800138000",
        currency="CNY",
        rate_amount=399,
        commission_amount=0,
        received_amount=399,
        income_method_id=1,
        remarks="approval_code=APP-001",
    )

    with pytest.raises(HostexTransportError):
        await client.create_reservation(request)

    assert attempts == 1
