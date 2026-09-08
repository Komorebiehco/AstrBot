import copy

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.utils.migra_helper import _migrate_agent_runner_config


def test_v428_migration_preserves_fork_search_and_agent_settings(tmp_path):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.pop("agent_runner")
    config["config_version"] = 2
    config["provider"] = [
        {
            "id": "primary",
            "type": "openai_chat_completion",
            "provider_type": "chat_completion",
        },
        {
            "id": "backup",
            "type": "openai_chat_completion",
            "provider_type": "chat_completion",
        },
    ]
    settings = config["provider_settings"]
    settings.update(
        default_provider_id="primary",
        fallback_chat_models=["backup"],
        max_agent_step=30,
        tool_call_timeout=60,
        web_search=True,
        websearch_provider="grok",
        websearch_grok_api_base="https://search.example/v1",
        websearch_grok_api_key="test-only-key",
        websearch_grok_model="test-search-model",
        computer_use_runtime="local",
    )
    search_before = {
        key: value for key, value in settings.items() if key.startswith("web")
    }

    assert _migrate_agent_runner_config(config)
    assert config["config_version"] == 3
    runner = config["agent_runner"]["config"]
    assert runner["model"]["provider_id"] == "primary"
    assert runner["model"]["fallback_provider_ids"] == ["backup"]
    assert runner["misc"]["max_steps"] == 30
    assert runner["misc"]["tool_call_timeout"] == 60
    assert all(
        config["provider_settings"][key] == value
        for key, value in search_before.items()
    )
    assert config["provider_settings"]["computer_use_runtime"] == "local"

    saved = AstrBotConfig(
        config_path=str(tmp_path / "profile.json"),
        default_config=DEFAULT_CONFIG,
    )
    saved.update(config)
    saved.save_config()
    reloaded = AstrBotConfig(
        config_path=str(tmp_path / "profile.json"),
        default_config=DEFAULT_CONFIG,
    )
    assert reloaded["agent_runner"] == config["agent_runner"]
    assert all(
        reloaded["provider_settings"][key] == value
        for key, value in search_before.items()
    )
