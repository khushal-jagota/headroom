from __future__ import annotations

from pathlib import Path

import pytest

from planner.core.config import load_config
from planner.core.errors import PlannerError


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


