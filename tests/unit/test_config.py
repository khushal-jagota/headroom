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
)


def test_config_defaults_expose_only_live_runtime_knobs() -> None:
    cfg = load_config(path=None, env={})

    assert isinstance(cfg, Config)
    assert cfg.dispatch_enabled is True
    assert cfg.tick_seconds == 60
    assert cfg.ws_heartbeat_ms == 15000
    assert cfg.trusted_ingress_provider is None
    assert cfg.trusted_ingress_allowed_login is None
    assert cfg.trusted_ingress_canonical_origin is None
    assert cfg.shutdown_grace_seconds == 30
    for name in _RETIRED_CONFIG_NAMES:
        assert not hasattr(cfg, name)


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


def test_startup_recovery_test_mode_switch_is_env_only_and_default_off() -> None:
    default_cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1"})
    enabled_cfg = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE": "1",
        },
    )
    production_cfg = load_config(
        path=None,
        env={"PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE": "1"},
    )

    assert default_cfg.run_startup_recovery_in_test_mode is False
    assert enabled_cfg.run_startup_recovery_in_test_mode is True
    assert production_cfg.run_startup_recovery_in_test_mode is False


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


def test_ws_heartbeat_ms_can_be_overridden_by_environment() -> None:
    cfg = load_config(path=None, env={"PLAN_WS_HEARTBEAT_MS": "125"})

    assert cfg.ws_heartbeat_ms == 125


def test_checked_in_config_exposes_ws_heartbeat_cadence() -> None:
    path = Path(__file__).parents[2] / "config.yaml"

    assert "ws_heartbeat_ms: 15000" in path.read_text()
    assert load_config(path=str(path), env={}).ws_heartbeat_ms == 15000
