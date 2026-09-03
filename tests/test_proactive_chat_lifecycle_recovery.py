from types import SimpleNamespace

import pytest

from bundled_plugins.astrbot_plugin_proactive_chat.core import plugin_lifecycle
from bundled_plugins.astrbot_plugin_proactive_chat.core.plugin_lifecycle import (
    LifecycleMixin,
)


class _Config(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.save_calls = 0

    def save_config(self):
        self.save_calls += 1


class _Scheduler:
    def __init__(self, timezone):
        self.timezone = timezone
        self.running = False

    def start(self):
        self.running = True

    def get_job(self, _job_id):
        return None


class _Plugin(LifecycleMixin):
    def __init__(self):
        self.config = _Config(
            {"friend_settings": {"enable": True, "session_list": []}}
        )
        self.context = SimpleNamespace(get_config=lambda: {"timezone": "UTC"})
        self.plugin_start_time = 0
        self.session_data = {}
        self.last_message_times = {}
        self.group_timers = {}
        self.auto_trigger_timers = {}
        self.web_admin_server = None
        self.notification_center = None
        self.telemetry = None
        self._heartbeat_task = None
        self._original_exception_handler = None
        self._exception_handler_installed = False
        self._start_time = 0
        self.scheduled = []

    async def _validate_config(self):
        return None

    async def _load_data_internal(self):
        self.session_data = {}

    def _normalize_session_data(self):
        return False

    def _parse_session_id(self, session_id):
        if session_id == "weixin_personal_iyxm:FriendMessage:friend@im.wechat":
            return "weixin_personal_iyxm", "FriendMessage", "friend@im.wechat"
        return None

    def _normalize_session_id(self, session_id):
        return session_id

    def _get_session_log_str(self, session_id, _session_config=None):
        return session_id

    async def _init_jobs_from_data(self):
        return None

    async def _schedule_next_chat_and_save(self, session_id):
        self.scheduled.append(session_id)

    async def _setup_auto_triggers_for_enabled_sessions(self):
        return None


@pytest.mark.asyncio
async def test_initialize_recovers_explicit_friend_session(monkeypatch):
    session_id = "weixin_personal_iyxm:FriendMessage:friend@im.wechat"
    plugin = _Plugin()
    monkeypatch.setenv("ASTRBOT_PROACTIVE_CHAT_FRIEND_SESSIONS", session_id)
    monkeypatch.setattr(plugin_lifecycle, "AsyncIOScheduler", _Scheduler)

    await plugin.initialize()

    assert plugin.config["friend_settings"]["session_list"] == [session_id]
    assert plugin.config.save_calls == 1
    assert plugin.scheduled == [session_id]
    assert plugin.scheduler.running is True
