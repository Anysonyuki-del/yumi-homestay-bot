from typing import Annotated, Protocol
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from homestay_bot.integrations.wecom.callback_crypto import (
    InvalidCallbackPayload,
    InvalidCallbackSignature,
    WeComCallbackCrypto,
)

router = APIRouter()


class CallbackSyncQueue(Protocol):
    """定义回调路由写入后台同步任务所需接口。"""

    async def enqueue_wecom_sync(self, token: str, open_kfid: str) -> None:
        """保存一项客服消息同步任务。"""


class WeComCallbackService:
    """解密企业微信回调，并把耗时同步操作转入队列。"""

    def __init__(
        self, crypto: WeComCallbackCrypto, queue: CallbackSyncQueue
    ) -> None:
        """注入加密校验器和持久化任务队列。"""
        self._crypto = crypto
        self._queue = queue

    @classmethod
    def from_credentials(
        cls,
        token: str,
        encoding_aes_key: str,
        receive_id: str,
        queue: CallbackSyncQueue,
    ) -> "WeComCallbackService":
        """从企业微信回调凭据创建服务。"""
        return cls(
            WeComCallbackCrypto(token, encoding_aes_key, receive_id),
            queue,
        )

    def verify_url(
        self,
        encrypted_echo: str,
        signature: str,
        timestamp: str,
        nonce: str,
    ) -> str:
        """验证首次配置请求并返回解密后的 echostr。"""
        plaintext = self._crypto.decrypt(
            encrypted_echo, signature, timestamp, nonce
        )
        return plaintext.decode("utf-8")

    async def verify_and_enqueue(
        self,
        encrypted_body: bytes,
        signature: str,
        timestamp: str,
        nonce: str,
    ) -> None:
        """验证事件回调并提取同步 Token 和客服账号。"""
        try:
            outer_root = ElementTree.fromstring(encrypted_body)
            encrypted = outer_root.findtext("Encrypt")
        except ElementTree.ParseError as error:
            raise InvalidCallbackPayload("企业微信外层 XML 无法解析") from error
        if not encrypted:
            raise InvalidCallbackPayload("企业微信回调缺少 Encrypt")

        plaintext = self._crypto.decrypt(encrypted, signature, timestamp, nonce)
        try:
            inner_root = ElementTree.fromstring(plaintext)
        except ElementTree.ParseError as error:
            raise InvalidCallbackPayload("企业微信内层 XML 无法解析") from error

        event = inner_root.findtext("Event")
        sync_token = inner_root.findtext("Token")
        open_kfid = inner_root.findtext("OpenKfId")
        if event != "kf_msg_or_event" or not sync_token or not open_kfid:
            raise InvalidCallbackPayload("企业微信回调不是可同步的客服事件")
        await self._queue.enqueue_wecom_sync(sync_token, open_kfid)


def get_callback_service(request: Request) -> WeComCallbackService:
    """从应用状态读取启动阶段装配的回调服务。"""
    service: object = getattr(request.app.state, "wecom_callback_service", None)
    if not isinstance(service, WeComCallbackService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="企业微信回调服务尚未配置",
        )
    return service


CallbackServiceDependency = Annotated[
    WeComCallbackService, Depends(get_callback_service)
]
RequiredQuery = Annotated[str, Query()]


@router.get("/callbacks/wecom")
async def verify_wecom_callback_url(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
    service: CallbackServiceDependency,
) -> Response:
    """响应企业微信首次保存回调 URL 时的校验请求。"""
    try:
        plaintext = service.verify_url(echostr, msg_signature, timestamp, nonce)
    except (InvalidCallbackSignature, InvalidCallbackPayload) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return Response(content=plaintext, media_type="text/plain")


@router.post("/callbacks/wecom")
async def receive_wecom_callback(
    request: Request,
    msg_signature: RequiredQuery,
    timestamp: RequiredQuery,
    nonce: RequiredQuery,
    service: CallbackServiceDependency,
) -> Response:
    """验签、解密并持久化回调任务，随后立即响应企业微信。"""
    try:
        await service.verify_and_enqueue(
            await request.body(), msg_signature, timestamp, nonce
        )
    except (InvalidCallbackSignature, InvalidCallbackPayload) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return Response(content="success", media_type="text/plain")
