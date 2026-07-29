import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.platform.sources.weixin_oc import weixin_oc_adapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter


def _make_adapter(*responses: dict) -> WeixinOCAdapter:
    adapter = object.__new__(WeixinOCAdapter)
    adapter.token = "token"
    adapter.account_id = "account"
    adapter.metadata = SimpleNamespace(id="weixin-test")
    adapter._context_tokens = {"user": "context-token"}
    adapter._sendmessage_lock = asyncio.Lock()
    adapter._last_sendmessage_request_at = 0.0
    adapter.client = SimpleNamespace(
        request_json=AsyncMock(side_effect=list(responses)),
    )
    adapter._cache_recent_message = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_sendmessage_retries_transient_prepare_failure(monkeypatch):
    adapter = _make_adapter(
        {"ret": -2, "errcode": 0, "errmsg": "prepare failed"},
        {"ret": 0, "errcode": 0},
    )
    sleep = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", sleep)

    result = await adapter._send_items_to_session(
        "user",
        [adapter._build_plain_text_item("hello")],
    )

    assert result is True
    assert adapter.client.request_json.await_count == 2
    sleep.assert_awaited_once_with(adapter.SENDMESSAGE_RETRY_DELAYS_S[0])
    first_payload = adapter.client.request_json.await_args_list[0].kwargs["payload"]
    second_payload = adapter.client.request_json.await_args_list[1].kwargs["payload"]
    assert first_payload == second_payload
    assert first_payload["msg"]["context_token"] == "context-token"
    adapter._cache_recent_message.assert_called_once()


@pytest.mark.asyncio
async def test_sendmessage_stops_after_transient_retry_limit(monkeypatch):
    responses = [
        {"ret": -2, "errcode": 0, "errmsg": "prepare failed"}
        for _ in range(len(WeixinOCAdapter.SENDMESSAGE_RETRY_DELAYS_S) + 1)
    ]
    adapter = _make_adapter(*responses)
    sleep = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", sleep)

    result = await adapter._send_items_to_session(
        "user",
        [adapter._build_plain_text_item("hello")],
    )

    assert result is False
    assert adapter.client.request_json.await_count == len(responses)
    assert sleep.await_count == len(adapter.SENDMESSAGE_RETRY_DELAYS_S)
    adapter._cache_recent_message.assert_not_called()


@pytest.mark.asyncio
async def test_sendmessage_does_not_retry_non_transient_error(monkeypatch):
    adapter = _make_adapter({"ret": 0, "errcode": 40003, "errmsg": "invalid user"})
    sleep = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", sleep)

    result = await adapter._send_items_to_session(
        "user",
        [adapter._build_plain_text_item("hello")],
    )

    assert result is False
    adapter.client.request_json.assert_awaited_once()
    sleep.assert_not_awaited()
    adapter._cache_recent_message.assert_not_called()


@pytest.mark.asyncio
async def test_sendmessage_serializes_concurrent_calls_and_spaces_requests():
    adapter = _make_adapter()
    adapter.SENDMESSAGE_MIN_INTERVAL_S = 0.05
    first_request_started = asyncio.Event()
    release_first_request = asyncio.Event()
    active_requests = 0
    max_active_requests = 0
    request_started_at: list[float] = []

    async def _request_json(*_args, **_kwargs):
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        request_started_at.append(time.monotonic())
        if len(request_started_at) == 1:
            first_request_started.set()
            await release_first_request.wait()
        active_requests -= 1
        return {"ret": 0, "errcode": 0}

    adapter.client.request_json = AsyncMock(side_effect=_request_json)
    first = asyncio.create_task(
        adapter._send_items_to_session(
            "user",
            [adapter._build_plain_text_item("first")],
        )
    )
    await first_request_started.wait()
    second = asyncio.create_task(
        adapter._send_items_to_session(
            "user",
            [adapter._build_plain_text_item("second")],
        )
    )
    await asyncio.sleep(0)

    assert adapter.client.request_json.await_count == 1
    release_first_request.set()
    assert await asyncio.gather(first, second) == [True, True]
    assert max_active_requests == 1
    assert len(request_started_at) == 2
    assert request_started_at[1] - request_started_at[0] >= 0.045
