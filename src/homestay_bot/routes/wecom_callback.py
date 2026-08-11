from collections.abc import AsyncIterator
from typing import Annotated, Any, Protocol

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from homestay_bot.integrations.wecom.callback_crypto import (
    InvalidCallbackPayload,
    InvalidCallbackSignature,
    WeComCallbackCrypto,
)

router = APIRouter()

# 企业微信回调只包含一层加密 XML，设置明确上限避免认证前读取无限请求体。
WECOM_CALLBACK_MAX_BODY_BYTES = 256 * 1024


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    """以流式方式读取请求体，超过上限时立即终止，避免无界缓冲。"""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="企业微信回调请求体过大",
            )

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="企业微信回调请求体过大",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
        except (ElementTree.ParseError, DefusedXmlException) as error:
            raise InvalidCallbackPayload("企业微信外层 XML 无法解析") from error
        if not encrypted:
            raise InvalidCallbackPayload("企业微信回调缺少 Encrypt")

        plaintext = self._crypto.decrypt(encrypted, signature, timestamp, nonce)
        try:
            inner_root = ElementTree.fromstring(plaintext)
        except (ElementTree.ParseError, DefusedXmlException) as error:
            raise InvalidCallbackPayload("企业微信内层 XML 无法解析") from error

        event = inner_root.findtext("Event")
        sync_token = inner_root.findtext("Token")
        open_kfid = inner_root.findtext("OpenKfId")
        if event != "kf_msg_or_event" or not sync_token or not open_kfid:
            raise InvalidCallbackPayload("企业微信回调不是可同步的客服事件")
        if len(sync_token) > 4096 or len(open_kfid) > 128:
            raise InvalidCallbackPayload("企业微信回调字段过长")
        await self._queue.enqueue_wecom_sync(sync_token, open_kfid)


async def get_callback_service(request: Request) -> AsyncIterator[WeComCallbackService]:
    """为完整回调请求租用当前 revision 的验签与解密服务。"""
    registry: Any = getattr(request.app.state, "runtime_client_registry", None)
    if registry is None or not callable(getattr(registry, "acquire", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="企业微信回调服务尚未配置",
        )
    async with registry.acquire() as bundle:
        service = bundle.wecom_callback_service
        if not isinstance(service, WeComCallbackService):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="企业微信回调服务尚未配置",
            )
        yield service


CallbackServiceDependency = Annotated[
    WeComCallbackService, Depends(get_callback_service)
]
RequiredQuery = Annotated[str, Query(min_length=1, max_length=256)]


@router.get("/callbacks/wecom")
async def verify_wecom_callback_url(
    msg_signature: RequiredQuery,
    timestamp: RequiredQuery,
    nonce: RequiredQuery,
    echostr: RequiredQuery,
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
            await _read_limited_body(request, WECOM_CALLBACK_MAX_BODY_BYTES),
            msg_signature,
            timestamp,
            nonce,
        )
    except (InvalidCallbackSignature, InvalidCallbackPayload) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return Response(content="success", media_type="text/plain")
