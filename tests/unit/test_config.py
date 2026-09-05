from __future__ import annotations

from pathlib import Path

import pytest

from planner.core.config import Config, load_config
from planner.core.errors import PlannerError

_RETIRED_CONFIG_NAMES = (
    "claim_ttl_seconds",
    "max_runs",
    "failure_limit",
    "run_max_seconds",
    "ws_poll_ms",
    "ws_heartbeat_ms",
    "ui_debounce_ms",
    "events_read_limit",
)


def test_config_defaults_expose_only_live_runtime_knobs() -> None:
    cfg = load_config(path=None, env={})

    assert isinstance(cfg, Config)
    assert cfg.dispatch_enabled is True
    assert cfg.tick_seconds == 60
    assert cfg.sse_heartbeat_ms == 15000
    assert cfg.trusted_ingress_provider is None
    assert cfg.trusted_ingress_allowed_login is None
    assert cfg.trusted_ingress_canonical_origin is None
    assert cfg.shutdown_grace_seconds == 30
    assert cfg.backup_dir == "data/backups"
    assert cfg.voice_transcription_base_url == "https://api.groq.com/openai/v1"
    assert cfg.voice_transcription_model == "whisper-large-v3-turbo"
    assert cfg.voice_transcription_api_key is None
    for name in _RETIRED_CONFIG_NAMES:
        assert not hasattr(cfg, name)


def test_voice_transcription_settings_can_be_overridden_by_environment() -> None:
    cfg = load_config(
        path=None,
        env={
            "PLAN_VOICE_TRANSCRIPTION_BASE_URL": "http://127.0.0.1:9/v1",
            "PLAN_VOICE_TRANSCRIPTION_MODEL": "whisper-large-v3",
            "PLAN_GROQ_API_KEY": "gsk-test",
        },
    )

    assert cfg.voice_transcription_base_url == "http://127.0.0.1:9/v1"
    assert cfg.voice_transcription_model == "whisper-large-v3"
    assert cfg.voice_transcription_api_key == "gsk-test"


def test_voice_transcription_key_is_environment_only(tmp_path: Path) -> None:
    """A secret in the checked-in yaml would be a leak, so the file cannot set it."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("voice_transcription_api_key: leaked\n")

    cfg = load_config(path=str(config_file), env={})

    assert cfg.voice_transcription_api_key is None


def test_trusted_ingress_config_loads_tailscale_contract() -> None:
    cfg = load_config(
        path=None,
        env={
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "khushal@example.com",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.tailnet.ts.net/",
        },
    )

    assert cfg.trusted_ingress_provider == "tailscale"
    assert cfg.trusted_ingress_allowed_login == "khushal@example.com"
    assert cfg.trusted_ingress_canonical_origin == "https://panels.tailnet.ts.net"


def test_trusted_ingress_config_allows_loopback_http_origin_only_in_test_mode() -> None:
    cfg = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "khushal@example.com",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "http://127.0.0.1:8767/",
        },
    )

    assert cfg.trusted_ingress_canonical_origin == "http://127.0.0.1:8767"


@pytest.mark.parametrize(
    "env",
    [
        {"PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "khushal@example.com"},
        {"PLAN_TRUSTED_INGRESS_PROVIDER": "cloudflare"},
        {"PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale"},
        {
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "khushal@example.com",
        },
        {
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "khushal@example.com",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "http://panels.tailnet.ts.net",
        },
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "khushal@example.com",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "http://panels.tailnet.ts.net",
        },
    ],
)
def test_trusted_ingress_config_rejects_partial_or_unsupported_contract(
    env: dict[str, str],
) -> None:
    with pytest.raises(PlannerError):
        load_config(path=None, env=env)


def test_retired_yaml_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "tick_seconds: 7",
                "claim_ttl_seconds: 1",
                "max_runs: 9",
                "failure_limit: 8",
                "run_max_seconds: 6",
            ]
        )
    )

    cfg = load_config(path=str(path), env={})

    assert cfg.tick_seconds == 7
    for name in _RETIRED_CONFIG_NAMES:
        assert not hasattr(cfg, name)


def test_retired_environment_keys_are_ignored() -> None:
    cfg = load_config(
        path=None,
        env={
            "PLAN_CLAIM_TTL_SECONDS": "1",
            "PLAN_MAX_RUNS": "9",
            "PLAN_FAILURE_LIMIT": "8",
            "PLAN_RUN_MAX_SECONDS": "6",
            "PLAN_TICK_SECONDS": "5",
        },
    )

    assert cfg.tick_seconds == 5
    for name in _RETIRED_CONFIG_NAMES:
        assert not hasattr(cfg, name)


def test_sse_heartbeat_ms_can_be_overridden_by_environment() -> None:
    cfg = load_config(path=None, env={"PLAN_SSE_HEARTBEAT_MS": "125"})

    assert cfg.sse_heartbeat_ms == 125


@pytest.mark.parametrize("heartbeat", ["0", "-1"])
def test_a_heartbeat_cadence_at_or_below_zero_refuses_to_load(heartbeat: str) -> None:
    # A zero interval would spin the change stream instead of keeping it quiet.
    with pytest.raises(PlannerError) as raised:
        load_config(path=None, env={"PLAN_SSE_HEARTBEAT_MS": heartbeat})

    assert "sse_heartbeat_ms must be greater than zero" in raised.value.message


def test_backup_directory_is_independently_configurable() -> None:
    cfg = load_config(path=None, env={"PLAN_BACKUP_DIR": "/operator-state/backups"})

    assert cfg.backup_dir == "/operator-state/backups"


