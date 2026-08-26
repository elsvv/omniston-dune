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


def test_load_settings_prefers_real_env_var_over_dotenv(tmp_path, monkeypatch):
    # A real environment variable (as set by CI from repository secrets) must
    # win over a value loaded from .env (as a developer's machine would
    # supply it). Getting this backwards would mean a stale local file
    # silently overrides CI secrets.
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "DUNE_API_KEY=from_dotenv\nDUNE_NAMESPACE=from_dotenv_ns\n"
    )
    monkeypatch.setattr(config, "find_dotenv", lambda: str(dotenv_path))
    monkeypatch.setenv("DUNE_API_KEY", "from_real_env")
    monkeypatch.delenv("DUNE_NAMESPACE", raising=False)

    settings = config.load_settings()

    assert settings.dune_api_key == "from_real_env"
    # A variable absent from the real environment is still filled in from
    # .env, proving the file actually gets loaded.
    assert settings.dune_namespace == "from_dotenv_ns"


def test_load_settings_tolerates_a_missing_dotenv_file(monkeypatch):
    # CI has no .env file at all; find_dotenv() returns "" when it can't find
    # one, and that must not blow up load_settings.
    monkeypatch.setattr(config, "find_dotenv", lambda: "")
    monkeypatch.setenv("DUNE_API_KEY", "abc123")
    monkeypatch.setenv("DUNE_NAMESPACE", "my_user")

    settings = config.load_settings()

    assert settings.dune_api_key == "abc123"
    assert settings.dune_namespace == "my_user"


def test_load_settings_with_explicit_env_never_touches_dotenv(monkeypatch):
    # Passing env= explicitly (as every other test in this file does) must
    # bypass .env loading entirely, or tests would start depending on
    # whatever .env happens to be on the developer's disk.
    def fail_if_called(*args, **kwargs):
        raise AssertionError("load_dotenv must not run when env is passed explicitly")

    monkeypatch.setattr(config, "load_dotenv", fail_if_called)

    settings = config.load_settings(
        {"DUNE_API_KEY": "abc123", "DUNE_NAMESPACE": "my_user"}
    )

    assert settings.dune_api_key == "abc123"
