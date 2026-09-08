import asyncio
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter
from bundled_plugins.astrbot_plugin_proactive_chat.core.chat_flow import (
    ProactiveCoreMixin,
)
from bundled_plugins.astrbot_plugin_proactive_chat.core.data_storage import StorageMixin
from bundled_plugins.astrbot_plugin_proactive_chat.core.message_sender import (
    SenderMixin,
)
from bundled_plugins.astrbot_plugin_proactive_chat.core.session_parser import (
    SessionMixin,
)
from bundled_plugins.astrbot_plugin_proactive_chat.core.task_scheduler import (
    SchedulerMixin,
)
from bundled_plugins.astrbot_plugin_proactive_chat.core.web_admin_server import (
    WebAdminServer,
)

SESSION = "weixin_personal_iyxm:FriendMessage:friend@im.wechat"


class Scheduler:
    def __init__(self):
        self.jobs = {}

    def get_jobs(self):
        return list(self.jobs.values())

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, function, trigger, **kwargs):
        self.jobs[kwargs["id"]] = SimpleNamespace(
            id=kwargs["id"],
            next_run_time=kwargs["run_date"],
            function=function,
            args=kwargs["args"],
        )

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


class Plugin(
    SessionMixin, SchedulerMixin, StorageMixin, SenderMixin, ProactiveCoreMixin
):
    def __init__(self, tmp_path):
        self.settings = {
            "enable": True,
            "session_list": [SESSION],
            "schedule_settings": {
                "min_interval_minutes": 30,
                "max_interval_minutes": 30,
                "max_unanswered_times": 4,
                "quiet_hours": "",
            },
        }
        self.config = {"friend_settings": self.settings}
        self.context = SimpleNamespace(
            platform_manager=SimpleNamespace(get_insts=lambda: []),
        )
        self.session_data = {}
        self.data_dir = tmp_path
        self.session_data_file = tmp_path / "sessions.json"
        self.data_lock = asyncio.Lock()
        self.timezone = ZoneInfo("UTC")
        self.scheduler = Scheduler()
        self.last_message_times = {}
        self.auto_trigger_timers = {}
        self.plugin_start_time = time.time() - 600
        self.manual_trigger_sessions = set()
        self.manual_trigger_results = {}
        self.web_admin_server = None
        self.telemetry = None
        self._prepare_llm_request = AsyncMock(
            return_value={
                "conv_id": "conversation",
                "history": [],
                "system_prompt": "",
                "session_id": SESSION,
            }
        )
        self._generate_llm_response = AsyncMock(return_value=("hello", "prompt"))
        self._send_proactive_message = AsyncMock(return_value=True)
        self._finalize_and_reschedule = AsyncMock()

    def _get_session_config(self, session_id):
        return self.settings if session_id == SESSION else None

    async def _is_chat_allowed(self, session_id):
        return True, "allowed"


@pytest.fixture
def plugin(tmp_path):
    return Plugin(tmp_path)


def test_explicit_platform_is_preserved_before_platform_start(plugin):
    assert plugin._normalize_session_id(SESSION) == SESSION
    plugin.session_data[SESSION] = {"next_trigger_time": time.time() + 600}
    assert plugin._normalize_session_data() is False
    assert SESSION in plugin.session_data


def test_default_route_uses_only_unambiguous_known_identity(plugin):
    legacy = SESSION.replace("weixin_personal_iyxm", "default")
    assert plugin._normalize_session_id(legacy) == SESSION
    plugin.session_data[SESSION.replace("weixin_personal_iyxm", "other")] = {}
    assert plugin._normalize_session_id(legacy) == legacy


@pytest.mark.asyncio
async def test_automatic_initial_schedule_is_saved_and_restorable(plugin):
    plugin.auto_trigger_timers[SESSION] = object()
    await plugin._handle_auto_trigger_callback(SESSION, 5)
    saved = json.loads(plugin.session_data_file.read_text(encoding="utf-8"))
    assert saved[SESSION]["next_trigger_time"] > time.time()
    next_run = plugin.scheduler.get_job(SESSION).next_run_time
    plugin.scheduler = Scheduler()
    await plugin._init_jobs_from_data()
    assert plugin.scheduler.get_job(SESSION).next_run_time == next_run


@pytest.mark.asyncio
async def test_missed_schedule_is_replaced_once_without_replaying(plugin):
    plugin.session_data[SESSION] = {"next_trigger_time": time.time() - 86400}
    await plugin._init_jobs_from_data()
    assert len(plugin.scheduler.get_jobs()) == 1
    assert plugin.scheduler.get_job(SESSION).next_run_time.timestamp() > time.time()
    plugin._send_proactive_message.assert_not_awaited()


def test_job_purge_does_not_cross_platform_boundaries(plugin):
    other = SESSION.replace("weixin_personal_iyxm", "other")
    for session in (SESSION, other):
        plugin.scheduler.add_job(
            None, "date", id=session, args=[], run_date=datetime.now()
        )
    plugin._purge_related_jobs(SESSION)
    assert plugin.scheduler.get_job(SESSION) is None
    assert plugin.scheduler.get_job(other) is not None


@pytest.mark.asyncio
async def test_unavailable_delivery_skips_llm_and_records_failure(plugin):
    plugin._get_delivery_status = lambda _: {
        "ready": False,
        "message": "微信通道尚未登录",
    }
    result = await plugin.check_and_chat(SESSION)
    assert result["ok"] is False
    plugin._prepare_llm_request.assert_not_awaited()
    plugin._send_proactive_message.assert_not_awaited()
    saved = json.loads(plugin.session_data_file.read_text(encoding="utf-8"))
    assert saved[SESSION]["last_execution"]["status"] == "failed"
    assert saved[SESSION]["last_execution"]["manual"] is False
    assert "next_trigger_time" in saved[SESSION]


@pytest.mark.asyncio
async def test_missing_context_still_reaches_automatic_delivery(plugin):
    adapter = object.__new__(WeixinOCAdapter)
    adapter.token = "test-login"
    adapter._context_tokens = {}
    adapter.metadata = SimpleNamespace(id="weixin_personal_iyxm")
    adapter._started_at = None
    adapter.status = PlatformStatus.RUNNING
    plugin.context.platform_manager.get_insts = lambda: [adapter]

    result = await plugin.check_and_chat(SESSION)

    assert result["ok"] is True
    plugin._send_proactive_message.assert_awaited_once()
    assert plugin.session_data[SESSION]["last_execution"]["manual"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, allowed", [(0, True), (4, False)])
async def test_unanswered_limit_zero_allows_unattended_delivery(plugin, limit, allowed):
    plugin.settings["schedule_settings"]["max_unanswered_times"] = limit
    plugin.session_data[SESSION] = {"unanswered_count": 20}
    plugin._get_delivery_status = lambda _: {"ready": True}

    result = await plugin.check_and_chat(SESSION)

    assert result["ok"] is allowed
    assert plugin._send_proactive_message.await_count == int(allowed)


@pytest.mark.asyncio
async def test_failed_send_never_finalizes_or_increments_unanswered(plugin):
    plugin._get_delivery_status = lambda _: {"ready": True}
    plugin._send_proactive_message.return_value = False
    result = await plugin.check_and_chat(SESSION, manual=True)
    assert result["ok"] is False
    plugin._finalize_and_reschedule.assert_not_awaited()
    assert plugin.session_data[SESSION].get("unanswered_count", 0) == 0
    assert plugin.session_data[SESSION]["last_execution"]["status"] == "failed"


@pytest.mark.asyncio
async def test_successful_execution_records_actual_result(plugin):
    plugin._get_delivery_status = lambda _: {"ready": True}
    result = await plugin.check_and_chat(SESSION, manual=True)
    assert result["ok"] is True
    plugin._finalize_and_reschedule.assert_awaited_once()
    assert plugin.session_data[SESSION]["last_execution"]["status"] == "success"
    assert not plugin.active_chat_sessions


@pytest.mark.asyncio
async def test_concurrent_execution_does_not_send_twice(plugin):
    plugin.active_chat_sessions = {SESSION}
    result = await plugin.check_and_chat(SESSION, manual=True)
    assert result["ok"] is False
    plugin._prepare_llm_request.assert_not_awaited()


def test_jobs_include_finished_execution_and_safe_delivery_status(plugin):
    plugin.session_data[SESSION] = {
        "last_execution": {"status": "failed", "message": "not delivered"},
    }
    server = object.__new__(WebAdminServer)
    server.plugin = plugin
    jobs = server._collect_jobs()
    assert len(jobs) == 1
    assert jobs[0]["next_run_time"] is None
    assert jobs[0]["last_execution"]["status"] == "failed"
    assert jobs[0]["delivery_status"]["code"] == "platform_unavailable"


def test_delivery_readiness_uses_exact_platform(plugin):
    platform = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="different"),
        status=PlatformStatus.RUNNING,
    )
    plugin.context.platform_manager.get_insts = lambda: [platform]
    assert plugin._get_delivery_status(SESSION)["ready"] is False


@pytest.mark.asyncio
async def test_storage_replacement_is_atomic(plugin, monkeypatch):
    from bundled_plugins.astrbot_plugin_proactive_chat.core import data_storage

    plugin.session_data = {SESSION: {"unanswered_count": 1}}
    await plugin._save_data_internal()
    original = plugin.session_data_file.read_bytes()
    plugin.session_data[SESSION]["unanswered_count"] = 2
    monkeypatch.setattr(data_storage.aio_os, "replace", AsyncMock(side_effect=OSError))
    await plugin._save_data_internal()
    assert plugin.session_data_file.read_bytes() == original
