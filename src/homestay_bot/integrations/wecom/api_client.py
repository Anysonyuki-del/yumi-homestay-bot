import time
from dataclasses import dataclass
from typing import Any

import httpx

from homestay_bot.integrations.wecom.schemas import SyncMessagePage

WECOM_BASE_URL = "https://qyapi.weixin.qq.com"


class WeComApiError(RuntimeError):
    """表示企业微信返回的稳定业务错误码。"""

    def __init__(self, error_code: int, message: str) -> None:
        """保存企业微信错误码，供上层决定是否重试。"""
        super().__init__(f"WeCom error {error_code}: {message}")
        self.error_code = error_code


@dataclass(frozen=True)
class CachedToken:
    """保存 access token 及提前失效时间。"""

    value: str
    expires_at: float


class WeComApiClient:
    """封装微信客服和内部应用共用的企业微信 API。"""

    def __init__(
        self,
        corp_id: str,
        kf_secret: str,
        agent_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存两类 Secret，并为测试注入 HTTP 传输层。"""
        self._corp_id = corp_id
        self._kf_secret = kf_secret
        self._agent_secret = agent_secret
        self._tokens: dict[str, CachedToken] = {}
        self._client = httpx.AsyncClient(
            base_url=WECOM_BASE_URL,
            timeout=10.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        """释放企业微信 HTTP 连接池。"""
        await self._client.aclose()

    async def _get_access_token(self, secret: str) -> str:
        """按 Secret 分别缓存客服和内部应用 access token。"""
        cached = self._tokens.get(secret)
        now = time.monotonic()
        if cached is not None and cached.expires_at > now:
            return cached.value

        response = await self._client.get(
            "/cgi-bin/gettoken",
            params={"corpid": self._corp_id, "corpsecret": secret},
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        token = str(payload["access_token"])
        expires_in = int(payload.get("expires_in", 7200))
        self._tokens[secret] = CachedToken(
            value=token,
            expires_at=now + max(expires_in - 300, 60),
        )
        return token

    @staticmethod
    def _raise_for_error(payload: dict[str, Any]) -> None:
        """把企业微信非零 errcode 转换为类型化异常。"""
        error_code = int(payload.get("errcode", -1))
        if error_code != 0:
            raise WeComApiError(error_code, str(payload.get("errmsg", "")))

    async def list_kf_account_ids(self) -> list[str]:
        """读取全部微信客服账号，供回调丢失时自动补拉消息。"""
        accounts = await self.list_kf_accounts()
        return [item["open_kfid"] for item in accounts]

    async def list_kf_accounts(self) -> list[dict[str, str]]:
        """读取微信客服账号的稳定 ID 和展示名称。"""
        access_token = await self._get_access_token(self._kf_secret)
        response = await self._client.get(
            "/cgi-bin/kf/account/list",
            params={"access_token": access_token},
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        return [
            {
                "open_kfid": str(item["open_kfid"]),
                "name": str(item.get("name", "")).strip(),
            }
            for item in payload.get("account_list", [])
            if item.get("open_kfid")
        ]

    async def get_kf_account_name(self, open_kfid: str) -> str | None:
        """按客服账号 ID 读取员工通知使用的客服名称。"""
        for account in await self.list_kf_accounts():
            if account["open_kfid"] == open_kfid:
                return account["name"] or None
        return None

    async def get_kf_customer_name(
        self,
        open_kfid: str,
        external_userid: str,
    ) -> str | None:
        """读取微信客服会话客人的昵称，查不到时由上层使用友好兜底名。"""
        access_token = await self._get_access_token(self._kf_secret)
        response = await self._client.post(
            "/cgi-bin/kf/customer/batchget",
            params={"access_token": access_token},
            json={
                "open_kfid": open_kfid,
                "external_userid_list": [external_userid],
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        customers = payload.get("customer_list", [])
        if not customers:
            return None
        nickname = str(customers[0].get("nickname", "")).strip()
        return nickname or None

    async def sync_messages(
        self,
        *,
        cursor: str,
        token: str,
        open_kfid: str,
        limit: int = 1000,
    ) -> SyncMessagePage:
        """使用回调 Token 和游标主动读取客服消息。"""
        access_token = await self._get_access_token(self._kf_secret)
        response = await self._client.post(
            "/cgi-bin/kf/sync_msg",
            params={"access_token": access_token},
            json={
                "cursor": cursor,
                "token": token,
                "limit": limit,
                "voice_format": 0,
                "open_kfid": open_kfid,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        return SyncMessagePage.model_validate(payload)

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
    ) -> str:
        """向微信客服会话发送一条文本消息并返回消息编号。"""
        access_token = await self._get_access_token(self._kf_secret)
        response = await self._client.post(
            "/cgi-bin/kf/send_msg",
            params={"access_token": access_token},
            json={
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgtype": "text",
                "text": {"content": content},
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        return str(payload["msgid"])

    async def upload_temporary_image(
        self,
        content: bytes,
        *,
        content_type: str,
    ) -> str:
        """使用客服凭据上传临时图片素材并返回 media_id。"""
        access_token = await self._get_access_token(self._kf_secret)
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }.get(content_type, "img")
        response = await self._client.post(
            "/cgi-bin/media/upload",
            params={
                "access_token": access_token,
                "type": "image",
            },
            files={
                "media": (
                    f"checkin-qr.{extension}",
                    content,
                    content_type,
                )
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        media_id = payload.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise WeComApiError(-1, "临时图片响应缺少 media_id")
        return media_id

    async def send_image(
        self,
        open_kfid: str,
        external_userid: str,
        media_id: str,
    ) -> str:
        """向准确微信客服会话发送一条图片消息。"""
        access_token = await self._get_access_token(self._kf_secret)
        response = await self._client.post(
            "/cgi-bin/kf/send_msg",
            params={"access_token": access_token},
            json={
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgtype": "image",
                "image": {"media_id": media_id},
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        return str(payload["msgid"])

    async def transfer_service_state(
        self,
        open_kfid: str,
        external_userid: str,
        employee_userid: str,
    ) -> str:
        """把客服会话转给指定企业微信员工接待。"""
        access_token = await self._get_access_token(self._kf_secret)
        response = await self._client.post(
            "/cgi-bin/kf/service_state/trans",
            params={"access_token": access_token},
            json={
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "service_state": 3,
                "servicer_userid": employee_userid,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        return str(payload["msg_code"])

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """通过企业微信内部应用向员工发送审批或紧急通知。"""
        access_token = await self._get_access_token(self._agent_secret)
        response = await self._client.post(
            "/cgi-bin/message/send",
            params={"access_token": access_token},
            json={
                "touser": "|".join(employee_userids),
                "msgtype": "text",
                "agentid": agent_id,
                "text": {"content": content},
                "safe": 0,
            },
        )
        response.raise_for_status()
        self._raise_for_error(response.json())

    async def get_employee_userid(self, code: str) -> str:
        """使用内部应用 access token 把 OAuth code 换成员 userid。"""
        access_token = await self._get_access_token(self._agent_secret)
        response = await self._client.get(
            "/cgi-bin/auth/getuserinfo",
            params={"access_token": access_token, "code": code},
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_for_error(payload)
        userid = payload.get("userid")
        if not isinstance(userid, str) or not userid:
            raise WeComApiError(-1, "OAuth 响应缺少企业成员 userid")
        return userid
