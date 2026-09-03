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
    adapter._pending_text_message_lock = asyncio.Lock()
    adapter._last_sendmessage_request_at = 0.0
    adapter.client = SimpleNamespace(
        request_json=AsyncMock(side_effect=list(responses)),
    )
    adapter._cache_recent_message = MagicMock()
    adapter._enqueue_pending_text_message = AsyncMock(return_value=False)
    return adapter


def test_resolve_context_user_id_recovers_unique_shortened_domain():
    adapter = _make_adapter()
    adapter._context_tokens = {
        "o9cq808EMikgb31Ir3fvML5CYoX8@im.wechat": "context-token"
    }

    resolved = adapter._resolve_context_user_id(
        "o9cq808EMikgb31Ir3fvML5CYoX8@im.wech"
    )

    assert resolved == "o9cq808EMikgb31Ir3fvML5CYoX8@im.wechat"


def test_resolve_context_user_id_keeps_ambiguous_candidate_unchanged():
    adapter = _make_adapter()
    adapter._context_tokens = {
        "user@im.wechat": "context-token-a",
        "user@im.wechat2": "context-token-b",
    }

    assert adapter._resolve_context_user_id("user@im.wech") == "user@im.wech"


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


@pytest.mark.asyncio
async def test_missing_context_queues_plain_text_for_delayed_delivery():
    adapter = _make_adapter()
    del adapter._enqueue_pending_text_message
    adapter._context_tokens = {}
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._save_account_state = AsyncMock()

    result = await adapter._send_items_to_session(
        "user",
        [adapter._build_plain_text_item("delayed")],
    )

    assert result is True
    assert [entry["text"] for entry in adapter._pending_text_messages] == ["delayed"]
    adapter._save_account_state.assert_awaited_once()
    adapter.client.request_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_by_session_can_require_immediate_delivery():
    adapter = _make_adapter()
    adapter._context_tokens = {}
    session = SimpleNamespace(
        session_id="user",
        allow_delayed_delivery=False,
    )

    with pytest.raises(RuntimeError, match="failed to send 1 message segment"):
        await adapter.send_by_session(
            session,
            weixin_oc_adapter.MessageChain(
                [weixin_oc_adapter.Plain("proactive message")]
            ),
        )

    adapter._enqueue_pending_text_message.assert_not_awaited()
    adapter.client.request_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_context_does_not_queue_media_payload():
    adapter = _make_adapter()
    del adapter._enqueue_pending_text_message
    adapter._context_tokens = {}
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._save_account_state = AsyncMock()

    result = await adapter._send_items_to_session(
        "user",
        [{"type": adapter.IMAGE_ITEM_TYPE, "image_item": {"media": {}}}],
    )

    assert result is False
    assert adapter._pending_text_messages == []
    adapter._save_account_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_exhaustion_queues_plain_text(monkeypatch):
    responses = [
        {"ret": -2, "errcode": 0, "errmsg": "prepare failed"}
        for _ in range(len(WeixinOCAdapter.SENDMESSAGE_RETRY_DELAYS_S) + 1)
    ]
    adapter = _make_adapter(*responses)
    del adapter._enqueue_pending_text_message
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._save_account_state = AsyncMock()
    monkeypatch.setattr(weixin_oc_adapter.asyncio, "sleep", AsyncMock())

    result = await adapter._send_items_to_session(
        "user",
        [adapter._build_plain_text_item("delayed")],
    )

    assert result is True
    assert [entry["text"] for entry in adapter._pending_text_messages] == ["delayed"]
    adapter._save_account_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_refresh_drains_and_removes_queued_text():
    adapter = _make_adapter()
    adapter._pending_text_messages = [
        {
            "id": "pending-1",
            "user_id": "user",
            "text": "delayed",
            "created_at": int(time.time()),
        }
    ]
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._send_items_to_session = AsyncMock(return_value=True)
    adapter._save_account_state = AsyncMock()

    await adapter._drain_pending_text_messages("user")

    assert adapter._pending_text_messages == []
    adapter._send_items_to_session.assert_awaited_once()
    assert adapter._send_items_to_session.await_args.kwargs["queue_on_failure"] is False
    adapter._save_account_state.assert_awaited_once()
