import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.core.platform.sources.weixin_oc import weixin_oc_adapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_client import WeixinOCClient


@pytest.mark.asyncio
async def test_client_online_state_notifications_use_official_endpoints():
    client = WeixinOCClient(
        adapter_id="weixin-test",
        base_url="https://example.com",
        cdn_base_url="https://cdn.example.com",
        api_timeout_ms=120_000,
        token="token",
    )
    client.request_json = AsyncMock(return_value={"ret": 0})

    await client.notify_start()
    await client.notify_stop()

    assert client.request_json.await_args_list[0].args == (
        "POST",
        "ilink/bot/msg/notifystart",
    )
    assert client.request_json.await_args_list[1].args == (
        "POST",
        "ilink/bot/msg/notifystop",
    )
    for call in client.request_json.await_args_list:
        assert call.kwargs["token_required"] is True
        assert call.kwargs["timeout_ms"] == 15_000
        assert call.kwargs["payload"]["base_info"] == {
            "channel_version": "astrbot",
            "bot_agent": "AstrBot",
        }


@pytest.mark.asyncio
async def test_adapter_registers_online_state_and_stops_cleanly():
    adapter = object.__new__(WeixinOCAdapter)
    adapter.token = "token"
    adapter._shutdown_event = asyncio.Event()
    adapter._login_session = None
    adapter.client = SimpleNamespace(
        token="token",
        notify_start=AsyncMock(return_value={"ret": 0}),
        notify_stop=AsyncMock(return_value={"ret": 0}),
        close=AsyncMock(),
    )
    adapter.meta = lambda: SimpleNamespace(id="weixin-test")
    adapter._cleanup_typing_tasks = AsyncMock()

    async def poll_once():
        adapter._shutdown_event.set()

    adapter._poll_inbound_updates = poll_once

    await adapter.run()

    adapter.client.notify_start.assert_awaited_once_with()
    adapter.client.notify_stop.assert_awaited_once_with()
    adapter.client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_adapter_retries_online_state_registration_after_failure(monkeypatch):
    adapter = object.__new__(WeixinOCAdapter)
    adapter.token = "token"
    adapter._shutdown_event = asyncio.Event()
    adapter._login_session = None
    adapter.client = SimpleNamespace(
        token="token",
        notify_start=AsyncMock(
            side_effect=[
                {"ret": -1, "errmsg": "temporary failure"},
                {"ret": 0},
            ]
        ),
        notify_stop=AsyncMock(return_value={"ret": 0}),
        close=AsyncMock(),
    )
    adapter.meta = lambda: SimpleNamespace(id="weixin-test")
    adapter._cleanup_typing_tasks = AsyncMock()
    monotonic_values = iter((0.0, 6.0, 12.0))
    monkeypatch.setattr(
        weixin_oc_adapter.time,
        "monotonic",
        lambda: next(monotonic_values, 1000.0),
    )
    poll_count = 0

    async def poll_once():
        nonlocal poll_count
        poll_count += 1
        if poll_count == 2:
            adapter._shutdown_event.set()

    adapter._poll_inbound_updates = poll_once

    await adapter.run()

    assert adapter.client.notify_start.await_count == 2
    adapter.client.notify_stop.assert_awaited_once_with()
    adapter.client.close.assert_awaited_once_with()
