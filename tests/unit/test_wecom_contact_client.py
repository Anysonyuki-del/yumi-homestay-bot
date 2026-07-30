import json

import httpx
import pytest

from homestay_bot.integrations.wecom.contact_client import WeComContactClient


@pytest.mark.asyncio
async def test_mark_tags_uses_contact_secret_without_logging_identity() -> None:
    """客户标签同步必须使用客户联系 Secret 和官方标记接口。"""
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        """记录令牌及标签请求。"""
        requests.append(request)
        if request.url.path.endswith("/gettoken"):
            assert request.url.params["corpsecret"] == "contact-secret"
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "access_token": "contact-token",
                    "expires_in": 7200,
                },
            )
        if request.url.path.endswith("/externalcontact/get"):
            assert request.url.params["external_userid"] == "wo-contact-1"
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "follow_user": [
                        {"userid": "XuKuang"},
                        {"userid": "YuMi"},
                    ],
                    "next_cursor": "",
                },
            )
        assert request.url.path.endswith(
            "/cgi-bin/externalcontact/mark_tag"
        )
        payload = json.loads(request.content)
        assert payload["external_userid"] == "wo-contact-1"
        assert payload["userid"] in {"XuKuang", "YuMi"}
        assert payload["add_tag"] == ["et-add"]
        assert payload["remove_tag"] == ["et-remove"]
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    client = WeComContactClient(
        "corp-id",
        "contact-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        await client.mark_tags(
            "wo-contact-1",
            add_tag_ids=["et-add"],
            remove_tag_ids=["et-remove"],
        )
    finally:
        await client.aclose()

    assert len(requests) == 4
