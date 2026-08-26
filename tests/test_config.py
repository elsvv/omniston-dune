import pytest

from omniston_dune import config


def test_load_settings_reads_env():
    settings = config.load_settings(
        {"DUNE_API_KEY": "abc123", "DUNE_NAMESPACE": "my_user"}
    )
    assert settings.dune_api_key == "abc123"
    assert settings.dune_namespace == "my_user"
    # 2026-03-25 UTC: a deliberate week of margin before history begins.
    assert settings.history_start_ts == 1774396800
    assert "omniston-dune" in settings.user_agent


def test_load_settings_names_the_missing_variable():
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_settings({"DUNE_NAMESPACE": "my_user"})
    assert "DUNE_API_KEY" in str(excinfo.value)


def test_load_settings_allows_overriding_history_start():
    settings = config.load_settings(
        {
            "DUNE_API_KEY": "abc123",
            "DUNE_NAMESPACE": "my_user",
            "HISTORY_START_TS": "1780000000",
        }
    )
    assert settings.history_start_ts == 1780000000


def test_load_settings_rejects_a_malformed_history_start():
    # A bare int() raises ValueError, which __main__ does not catch: the
    # operator would get a traceback instead of a message naming the typo.
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_settings(
            {
                "DUNE_API_KEY": "abc123",
                "DUNE_NAMESPACE": "my_user",
                "HISTORY_START_TS": "2026-04-01",
            }
        )
    message = str(excinfo.value)
    assert "HISTORY_START_TS" in message
    assert "'2026-04-01'" in message
