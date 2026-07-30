import httpx
import pytest

from homestay_bot.integrations.wecom.api_client import WeComApiClient


@pytest.mark.asyncio
async def test_list_kf_account_ids_uses_customer_service_secret() -> None:
    """定时补拉应自动发现全部微信客服账号。"""
    token_secrets: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            token_secrets.append(request.url.params["corpsecret"])
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "access_token": "kf-access",
                    "expires_in": 7200,
                },
            )
        assert request.url.path.endswith("/cgi-bin/kf/account/list")
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "account_list": [
                    {"open_kfid": "wk-1", "name": "客服一"},
                    {"open_kfid": "wk-2", "name": "客服二"},
                ],
            },
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        account_ids = await client.list_kf_account_ids()
    finally:
        await client.aclose()

    assert account_ids == ["wk-1", "wk-2"]
    assert token_secrets == ["kf-secret"]


@pytest.mark.asyncio
async def test_sync_messages_uses_kf_token_and_cursor() -> None:
    """读取客服消息必须先取凭证，再提交回调中的同步 Token。"""
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/gettoken"):
            assert request.url.params["corpid"] == "corp-id"
            assert request.url.params["corpsecret"] == "kf-secret"
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "access", "expires_in": 7200},
            )
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "next_cursor": "cursor-2",
                "has_more": 0,
                "msg_list": [],
            },
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )
    result = await client.sync_messages(
        cursor="cursor-1", token="sync-token", open_kfid="wk-1"
    )

    assert result.next_cursor == "cursor-2"
    assert requests[1].url.path.endswith("/cgi-bin/kf/sync_msg")
    assert b"sync-token" in requests[1].content


@pytest.mark.asyncio
async def test_sync_messages_accepts_event_without_top_level_account_id() -> None:
    """企业微信事件仅在 event 内含客服账号时，整页消息仍应可解析。"""

    def responder(request: httpx.Request) -> httpx.Response:
        """返回真实企业微信 enter_session 事件的字段结构。"""
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "access_token": "access",
                    "expires_in": 7200,
                },
            )
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "next_cursor": "cursor-2",
                "has_more": 0,
                "msg_list": [
                    {
                        "msgid": "event-1",
                        "send_time": 1785298008,
                        "origin": 4,
                        "msgtype": "event",
                        "event": {
                            "event_type": "enter_session",
                            "open_kfid": "wk-1",
                            "external_userid": "wm-1",
                        },
                    }
                ],
            },
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        page = await client.sync_messages(
            cursor="", token="", open_kfid="wk-1"
        )
    finally:
        await client.aclose()

    assert page.msg_list[0].open_kfid is None
    assert page.msg_list[0].event == {
        "event_type": "enter_session",
        "open_kfid": "wk-1",
        "external_userid": "wm-1",
    }


@pytest.mark.asyncio
async def test_send_text_uses_customer_service_endpoint() -> None:
    """机器人回复必须发往指定客服账号和外部联系人。"""

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "access", "expires_in": 7200},
            )
        assert request.url.path.endswith("/cgi-bin/kf/send_msg")
        assert b"wk-1" in request.content
        assert b"wm-1" in request.content
        return httpx.Response(
            200, json={"errcode": 0, "errmsg": "ok", "msgid": "MSG-1"}
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )

    message_id = await client.send_text("wk-1", "wm-1", "您好")

    assert message_id == "MSG-1"


@pytest.mark.asyncio
async def test_upload_temporary_image_uses_kf_token_and_multipart() -> None:
    """入住二维码应使用客服凭据上传为企业微信临时图片素材。"""
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        """记录令牌和 multipart 上传请求。"""
        requests.append(request)
        if request.url.path.endswith("/gettoken"):
            assert request.url.params["corpsecret"] == "kf-secret"
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "access_token": "kf-access",
                    "expires_in": 7200,
                },
            )
        assert request.url.path.endswith("/cgi-bin/media/upload")
        assert request.url.params["type"] == "image"
        assert b"image/png" in request.content
        assert b"PNG-CONTENT" in request.content
        return httpx.Response(
            200,
            json={"errcode": 0, "type": "image", "media_id": "MEDIA-1"},
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        media_id = await client.upload_temporary_image(
            b"PNG-CONTENT",
            content_type="image/png",
        )
    finally:
        await client.aclose()

    assert media_id == "MEDIA-1"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_send_image_uses_customer_service_endpoint() -> None:
    """二维码图片消息必须发送给准确客服账号和客户身份。"""

    def responder(request: httpx.Request) -> httpx.Response:
        """返回图片消息的企业微信编号。"""
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "access_token": "kf-access",
                    "expires_in": 7200,
                },
            )
        assert request.url.path.endswith("/cgi-bin/kf/send_msg")
        assert b'"msgtype":"image"' in request.content
        assert b'"media_id":"MEDIA-1"' in request.content
        assert b"wk-1" in request.content
        assert b"wm-1" in request.content
        return httpx.Response(
            200,
            json={"errcode": 0, "msgid": "IMAGE-MSG-1"},
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        message_id = await client.send_image(
            "wk-1",
            "wm-1",
            "MEDIA-1",
        )
    finally:
        await client.aclose()

    assert message_id == "IMAGE-MSG-1"


@pytest.mark.asyncio
async def test_send_internal_message_uses_agent_secret() -> None:
    """内部审批通知必须使用自建应用 Secret 和 AgentID。"""
    token_secrets: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            token_secrets.append(request.url.params["corpsecret"])
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "access_token": "agent-access",
                    "expires_in": 7200,
                },
            )
        assert request.url.path.endswith("/cgi-bin/message/send")
        assert b'"agentid":100001' in request.content
        assert b"staff-1" in request.content
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )

    await client.send_internal_text(
        agent_id=100001,
        employee_userids=["staff-1"],
        content="有新的预订待审批",
    )

    assert token_secrets == ["agent-secret"]


@pytest.mark.asyncio
async def test_transfer_service_state_assigns_employee() -> None:
    """转人工时应指定客服账号、客人和接待员工。"""

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "access", "expires_in": 7200},
            )
        assert request.url.path.endswith("/cgi-bin/kf/service_state/trans")
        assert b'"service_state":3' in request.content
        assert b"staff-1" in request.content
        return httpx.Response(
            200, json={"errcode": 0, "errmsg": "ok", "msg_code": "CODE"}
        )

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )

    code = await client.transfer_service_state("wk-1", "wm-1", "staff-1")

    assert code == "CODE"


@pytest.mark.asyncio
async def test_oauth_code_resolves_employee_userid_with_agent_token() -> None:
    """员工 OAuth code 必须使用内部应用凭据换取企业成员身份。"""
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/gettoken"):
            assert request.url.params["corpsecret"] == "agent-secret"
            return httpx.Response(
                200,
                json={"errcode": 0, "access_token": "agent-token", "expires_in": 7200},
            )
        assert request.url.path.endswith("/cgi-bin/auth/getuserinfo")
        assert request.url.params["access_token"] == "agent-token"
        assert request.url.params["code"] == "oauth-code"
        return httpx.Response(200, json={"errcode": 0, "userid": "staff-1"})

    client = WeComApiClient(
        "corp-id",
        "kf-secret",
        "agent-secret",
        transport=httpx.MockTransport(responder),
    )

    userid = await client.get_employee_userid("oauth-code")

    assert userid == "staff-1"
    assert len(requests) == 2
