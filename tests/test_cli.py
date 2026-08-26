import pytest

from omniston_dune import __main__ as cli


def test_parse_args_defaults_to_no_query_ids():
    args = cli.parse_args([])
    assert args.query_ids == []
    assert args.dry_run is False


def test_parse_args_reads_comma_separated_query_ids():
    args = cli.parse_args(["--query-ids", "8309209,8309210"])
    assert args.query_ids == [8309209, 8309210]


def test_parse_args_supports_dry_run():
    assert cli.parse_args(["--dry-run"]).dry_run is True


def test_main_dry_run_fetches_without_publishing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli.config, "load_settings",
        lambda: cli.config.Settings("k", "ns", "ua", 1000),
    )
    monkeypatch.setattr(
        cli.pipeline, "build_datasets",
        lambda settings, **kwargs: calls.append("fetch") or {"t": [{"a": 1}]},
    )
    monkeypatch.setattr(
        cli.pipeline, "publish",
        lambda *a, **k: calls.append("publish") or {},
    )
    assert cli.main(["--dry-run"]) == 0
    assert calls == ["fetch"]


def test_main_returns_1_on_config_error(monkeypatch):
    def raise_config_error():
        raise cli.config.ConfigError("Missing required environment variable DUNE_API_KEY.")

    monkeypatch.setattr(cli.config, "load_settings", raise_config_error)
    assert cli.main([]) == 1


def test_main_propagates_unexpected_exceptions(monkeypatch):
    def raise_unexpected():
        raise ValueError("boom")

    monkeypatch.setattr(cli.config, "load_settings", raise_unexpected)
    with pytest.raises(ValueError, match="boom"):
        cli.main([])
