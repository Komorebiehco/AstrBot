from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from bundled_plugins.astrbot_plugin_proactive_chat.core.notification_center import (
    NotificationCenter,
)


@pytest.mark.asyncio
async def test_notification_center_stops_polling_after_access_denied(tmp_path):
    plugin = SimpleNamespace(
        config={"notification_settings": {"enabled": True}},
        data_dir=tmp_path,
        web_admin_server=None,
    )
    center = NotificationCenter(plugin)
    calls = 0

    async def fetch_denied():
        nonlocal calls
        calls += 1
        denied = HTTPError(
            "https://example.com/notifications",
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )
        raise RuntimeError("notification request failed") from denied

    center._fetch_remote_items = fetch_denied

    await center.start()
    await center.refresh()

    assert calls == 1
    assert center._remote_sync_blocked is True
    assert center._poll_task is None
