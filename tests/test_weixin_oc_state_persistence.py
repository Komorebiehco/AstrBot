import asyncio
from unittest.mock import AsyncMock

import pytest

from astrbot.core.platform.sources.weixin_oc import weixin_oc_adapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter


class _Config(dict):
    def __init__(self, *args, calls: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._calls = calls

    async def save_config_async(self) -> bool:
        self._calls.append("save_config_async")
        return True


@pytest.mark.asyncio
async def test_poll_inbound_updates_saves_new_context_token():
    calls: list[str] = []

    class Client:
        async def request_json(self, *args, **kwargs):
            return {"ret": 0, "msgs": [{"from_user_id": "user"}]}

    adapter = object.__new__(WeixinOCAdapter)
    adapter.client = Client()
    adapter._sync_buf = "sync-buffer"
    adapter._context_tokens_dirty = False
    # Even a pending drain marker left by the old implementation must not replay.
    adapter._pending_drain_user_ids = {"user"}
    adapter._shutdown_event = asyncio.Event()
    adapter.long_poll_timeout_ms = 35_000
    adapter._is_successful_api_payload = lambda data: True

    async def handle_inbound_message(msg):
        adapter._context_tokens_dirty = True
        calls.append("handle")

    async def save_account_state():
        calls.append("save")

    adapter._handle_inbound_message = handle_inbound_message
    adapter._save_account_state = save_account_state
    adapter._drain_pending_text_messages = AsyncMock()

    await adapter._poll_inbound_updates()

    assert calls == ["handle", "save"]
    adapter._drain_pending_text_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_account_state_uses_async_config_persistence(monkeypatch):
    calls: list[str] = []
    config = _Config(
        {
            "platform": [
                {
                    "id": "weixin-test",
                    "type": "weixin_oc",
                }
            ]
        },
        calls=calls,
    )

    monkeypatch.setattr(weixin_oc_adapter, "astrbot_config", config)

    adapter = object.__new__(WeixinOCAdapter)
    adapter.config = {"id": "weixin-test", "type": "weixin_oc"}
    adapter.token = "token"
    adapter.account_id = "account"
    adapter._sync_buf = "sync-buffer"
    adapter.base_url = "https://example.com"
    adapter._context_tokens = {"user": "context-token"}
    adapter._context_tokens_dirty = True
    adapter._context_tokens_revision = 0
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._pending_text_message_lock = asyncio.Lock()
    adapter._sync_client_state = lambda: None

    await adapter._save_account_state()

    assert calls == ["save_config_async"]
    assert config["platform"][0]["weixin_oc_sync_buf"] == "sync-buffer"
    assert adapter._context_tokens_dirty is False


@pytest.mark.asyncio
async def test_save_account_state_keeps_dirty_flag_for_new_context_token(monkeypatch):
    save_started = asyncio.Event()
    finish_save = asyncio.Event()

    class BlockingConfig(dict):
        async def save_config_async(self) -> bool:
            save_started.set()
            await finish_save.wait()
            return True

    config = BlockingConfig(
        {
            "platform": [
                {
                    "id": "weixin-test",
                    "type": "weixin_oc",
                }
            ]
        }
    )
    monkeypatch.setattr(weixin_oc_adapter, "astrbot_config", config)

    adapter = object.__new__(WeixinOCAdapter)
    adapter.config = {"id": "weixin-test", "type": "weixin_oc"}
    adapter.token = "token"
    adapter.account_id = "account"
    adapter._sync_buf = "sync-buffer"
    adapter.base_url = "https://example.com"
    adapter._context_tokens = {"user": "old-context-token"}
    adapter._context_tokens_dirty = True
    adapter._context_tokens_revision = 0
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._pending_text_message_lock = asyncio.Lock()
    adapter._sync_client_state = lambda: None

    save_task = asyncio.create_task(adapter._save_account_state())
    save_started_task = asyncio.create_task(save_started.wait())
    done, _ = await asyncio.wait(
        {save_task, save_started_task},
        timeout=5,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if save_task in done:
        await save_task
    assert save_started_task in done
    adapter._context_tokens["user"] = "new-context-token"
    adapter._context_tokens_revision += 1
    adapter._context_tokens_dirty = True
    finish_save.set()
    await save_task

    assert adapter._context_tokens_dirty is True


@pytest.mark.asyncio
async def test_save_account_state_keeps_dirty_flag_after_context_token_aba(
    monkeypatch,
):
    save_started = asyncio.Event()
    finish_save = asyncio.Event()

    class BlockingConfig(dict):
        async def save_config_async(self) -> bool:
            save_started.set()
            await finish_save.wait()
            return True

    config = BlockingConfig(
        {
            "platform": [
                {
                    "id": "weixin-test",
                    "type": "weixin_oc",
                }
            ]
        }
    )
    monkeypatch.setattr(weixin_oc_adapter, "astrbot_config", config)

    adapter = object.__new__(WeixinOCAdapter)
    adapter.config = {"id": "weixin-test", "type": "weixin_oc"}
    adapter.token = "token"
    adapter.account_id = "account"
    adapter._sync_buf = "sync-buffer"
    adapter.base_url = "https://example.com"
    adapter._context_tokens = {"user": "context-a"}
    adapter._context_tokens_dirty = True
    adapter._context_tokens_revision = 0
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = False
    adapter._pending_text_messages_revision = 0
    adapter._pending_text_message_lock = asyncio.Lock()
    adapter._sync_client_state = lambda: None

    save_task = asyncio.create_task(adapter._save_account_state())
    await asyncio.wait_for(save_started.wait(), timeout=5)
    adapter._context_tokens["user"] = "context-b"
    adapter._context_tokens_revision += 1
    adapter._context_tokens["user"] = "context-a"
    adapter._context_tokens_revision += 1
    adapter._context_tokens_dirty = True
    finish_save.set()
    await save_task

    assert adapter._context_tokens_dirty is True


@pytest.mark.asyncio
async def test_save_account_state_keeps_dirty_flag_for_new_pending_message(
    monkeypatch,
):
    save_started = asyncio.Event()
    finish_save = asyncio.Event()

    class BlockingConfig(dict):
        async def save_config_async(self) -> bool:
            save_started.set()
            await finish_save.wait()
            return True

    config = BlockingConfig(
        {
            "platform": [
                {
                    "id": "weixin-test",
                    "type": "weixin_oc",
                }
            ]
        }
    )
    monkeypatch.setattr(weixin_oc_adapter, "astrbot_config", config)

    adapter = object.__new__(WeixinOCAdapter)
    adapter.config = {"id": "weixin-test", "type": "weixin_oc"}
    adapter.token = "token"
    adapter.account_id = "account"
    adapter._sync_buf = "sync-buffer"
    adapter.base_url = "https://example.com"
    adapter._context_tokens = {"user": "context-token"}
    adapter._context_tokens_dirty = False
    adapter._context_tokens_revision = 0
    adapter._pending_text_messages = []
    adapter._pending_text_messages_dirty = True
    adapter._pending_text_messages_revision = 0
    adapter._pending_text_message_lock = asyncio.Lock()
    adapter._sync_client_state = lambda: None

    save_task = asyncio.create_task(adapter._save_account_state())
    await asyncio.wait_for(save_started.wait(), timeout=5)
    adapter._pending_text_messages.append(
        {
            "id": "pending-1",
            "user_id": "user",
            "text": "delayed",
            "created_at": 1_788_093_600,
        }
    )
    adapter._pending_text_messages_revision += 1
    adapter._pending_text_messages_dirty = True
    finish_save.set()
    await save_task

    assert adapter._pending_text_messages_dirty is True
