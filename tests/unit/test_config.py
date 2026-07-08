from __future__ import annotations

from pathlib import Path

from planner.core.config import Config, load_config

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
    for name in _RETIRED_CONFIG_NAMES:
        assert not hasattr(cfg, name)


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
