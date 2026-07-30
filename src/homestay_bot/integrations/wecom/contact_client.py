import time

import httpx

WECOM_BASE_URL = "https://qyapi.weixin.qq.com"


class WeComContactError(RuntimeError):
    """表示企业微信客户联系接口返回稳定错误码。"""

    def __init__(self, error_code: int) -> None:
        """只保存错误码，避免异常正文携带外部联系人身份。"""
        super().__init__(f"WeCom contact error {error_code}")
        self.error_code = error_code


class WeComContactClient:
    """使用可选客户联系 Secret 同步企业微信客户标签。"""

    def __init__(
        self,
        corp_id: str,
        contact_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """保存企业凭据并允许测试注入传输层。"""
        self._corp_id = corp_id
        self._contact_secret = contact_secret
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._client = httpx.AsyncClient(
            base_url=WECOM_BASE_URL,
            timeout=10.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        """释放 HTTP 连接池。"""
        await self._client.aclose()

    async def mark_tags(
        self,
        external_userid: str,
        *,
        add_tag_ids: list[str],
        remove_tag_ids: list[str],
    ) -> None:
        """调用官方客户联系接口增删企业微信标签。"""
        if not add_tag_ids and not remove_tag_ids:
            return
        access_token = await self._get_access_token()
        follow_userids = await self._follow_userids(
            access_token,
            external_userid,
        )
        for userid in follow_userids:
            response = await self._client.post(
                "/cgi-bin/externalcontact/mark_tag",
                params={"access_token": access_token},
                json={
                    "userid": userid,
                    "external_userid": external_userid,
                    "add_tag": add_tag_ids,
                    "remove_tag": remove_tag_ids,
                },
            )
            response.raise_for_status()
            payload = response.json()
            error_code = int(payload.get("errcode", -1))
            if error_code != 0:
                raise WeComContactError(error_code)

    async def _follow_userids(
        self,
        access_token: str,
        external_userid: str,
    ) -> list[str]:
        """分页读取客户关系中的跟进员工，标签需按关系分别写入。"""
        cursor = ""
        userids: list[str] = []
        seen: set[str] = set()
        while True:
            response = await self._client.get(
                "/cgi-bin/externalcontact/get",
                params={
                    "access_token": access_token,
                    "external_userid": external_userid,
                    "cursor": cursor,
                },
            )
            response.raise_for_status()
            payload = response.json()
            error_code = int(payload.get("errcode", -1))
            if error_code != 0:
                raise WeComContactError(error_code)
            for item in payload.get("follow_user", []):
                userid = str(item.get("userid", "")).strip()
                if userid and userid not in seen:
                    seen.add(userid)
                    userids.append(userid)
            cursor = str(payload.get("next_cursor", "")).strip()
            if not cursor:
                break
        if not userids:
            raise WeComContactError(-2)
        return userids

    async def _get_access_token(self) -> str:
        """缓存客户联系 access token 并提前五分钟刷新。"""
        now = time.monotonic()
        if self._token is not None and self._token_expires_at > now:
            return self._token
        response = await self._client.get(
            "/cgi-bin/gettoken",
            params={
                "corpid": self._corp_id,
                "corpsecret": self._contact_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        error_code = int(payload.get("errcode", -1))
        if error_code != 0:
            raise WeComContactError(error_code)
        self._token = str(payload["access_token"])
        expires_in = int(payload.get("expires_in", 7200))
        self._token_expires_at = now + max(expires_in - 300, 60)
        return self._token
