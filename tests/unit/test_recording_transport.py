"""外联台账传输层：成功、失败与抛异常都要留下记录。"""

import httpx
import pytest

from homestay_bot.services.outbound_url_policy import RecordingTransport


class _Inner(httpx.AsyncBaseTransport):
    """按预设返回响应或抛异常的底层传输桩。"""

    def __init__(self, result: object) -> None:
        self._result = result
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self._result, Exception):
            raise self._result
        return httpx.Response(int(self._result), request=request)

    async def aclose(self) -> None:
        self.closed = True


def _transport(result: object) -> tuple[RecordingTransport, list[object]]:
    records: list[object] = []

    async def record(entry: object) -> None:
        records.append(entry)

    return RecordingTransport(_Inner(result), provider="deepseek", record=record), records


@pytest.mark.asyncio
async def test_a_successful_call_is_recorded() -> None:
    """正常调用记一条成功台账，业务码取 HTTP 状态。"""
    transport, records = _transport(200)

    response = await transport.handle_async_request(
        httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    )

    assert response.status_code == 200
    assert len(records) == 1
    assert (records[0].provider, records[0].method) == ("deepseek", "POST")
    assert records[0].path == "/chat/completions"
    assert records[0].business_code == 200
    assert records[0].succeeded is True


@pytest.mark.asyncio
async def test_an_http_error_is_recorded_as_failed() -> None:
    """4xx/5xx 记成失败，否则台账只能证明「调过」不能证明「成了」。"""
    transport, records = _transport(429)

    await transport.handle_async_request(
        httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    )

    assert records[0].succeeded is False
    assert records[0].business_code == 429


@pytest.mark.asyncio
async def test_a_transport_exception_is_recorded_and_reraised() -> None:
    """超时与连接失败必须上账并原样抛出。

    这类失败根本拿不到响应，只挂在响应回调上会把它们整类漏掉——而它们恰恰是
    排障时最想看见的那一类。
    """
    boom = httpx.ConnectTimeout("timed out")
    transport, records = _transport(boom)

    with pytest.raises(httpx.ConnectTimeout):
        await transport.handle_async_request(
            httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        )

    assert len(records) == 1
    assert records[0].succeeded is False
    assert records[0].business_code is None


@pytest.mark.asyncio
async def test_a_failing_ledger_never_breaks_the_call() -> None:
    """台账写不进去也不能影响业务调用；可观测性不该拖垮功能。"""
    async def record(_entry: object) -> None:
        raise RuntimeError("台账数据库不可用")

    transport = RecordingTransport(_Inner(200), provider="deepseek", record=record)

    response = await transport.handle_async_request(
        httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_ledger_never_stores_the_query_string() -> None:
    """只取路径：查询串可能带参数，进台账等于开一条新的外泄路径。"""
    transport, records = _transport(200)

    await transport.handle_async_request(
        httpx.Request("GET", "https://api.deepseek.com/v1/models?key=abc&phone=13800000000")
    )

    assert records[0].path == "/v1/models"
    assert "13800000000" not in records[0].path
    assert "abc" not in records[0].path
