"""The Hermes inventory door exists in an installed Panels distribution and runs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _isolated_environment(
    tmp_path: Path, *, fake_hermes_package: Path, payload: dict[str, Any], calls: Path
) -> dict[str, str]:
    runtime = tmp_path / "runtime"
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(fake_hermes_package),
        "HERMES_HOME": str(runtime / "hermes-home"),
        "PLAN_HERMES_HOME": str(runtime / "hermes-home"),
        "PLAN_DB_PATH": str(runtime / "planner.db"),
        "PLAN_LOGS_DIR": str(runtime / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(runtime / "dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(runtime / "server.sock"),
        "PANELS_TEST_HERMES_INVENTORY_PAYLOAD": json.dumps(payload),
        "PANELS_TEST_HERMES_INVENTORY_CALLS": str(calls),
    }


def test_installed_distribution_runs_the_hermes_inventory_door(
    tmp_path: Path,
) -> None:
    wheel_directory = tmp_path / "wheels"
    wheel_directory.mkdir()
    build_source = tmp_path / "source"
    build_source.mkdir()
    shutil.copy2(REPOSITORY_ROOT / "pyproject.toml", build_source / "pyproject.toml")
    shutil.copytree(REPOSITORY_ROOT / "src", build_source / "src")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_directory),
            str(build_source),
        ],
        check=True,
        cwd=build_source,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_directory.glob("planner-*.whl"))
    installed = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    door = (
        installed
        / "planner"
        / "conversation"
        / "backends"
        / "hermes_model_catalog.py"
    )
    assert door.is_file()

    fake_package = tmp_path / "fake-hermes"
    inventory_package = fake_package / "hermes_cli"
    inventory_package.mkdir(parents=True)
    (inventory_package / "__init__.py").write_text("", encoding="utf-8")
    (inventory_package / "inventory.py").write_text(
        """
import json
import os
from pathlib import Path

def load_picker_context():
    print("provider status which must not corrupt stdout")
    return object()

def build_models_payload(context, **kwargs):
    del context
    Path(os.environ["PANELS_TEST_HERMES_INVENTORY_CALLS"]).write_text(
        json.dumps(kwargs), encoding="utf-8"
    )
    return json.loads(os.environ["PANELS_TEST_HERMES_INVENTORY_PAYLOAD"])
""".lstrip(),
        encoding="utf-8",
    )
    (inventory_package / "models.py").write_text(
        """
_KNOWN_PROVIDER_NAMES = {"openai-codex", "custom"}

def parse_model_input(raw, current_provider):
    stripped = raw.strip()
    colon = stripped.find(":")
    if colon > 0:
        provider_part = stripped[:colon].strip().lower()
        model_part = stripped[colon + 1:].strip()
        if provider_part and model_part and provider_part in _KNOWN_PROVIDER_NAMES:
            if provider_part == "custom" and ":" in model_part:
                custom_name, actual_model = model_part.split(":", 1)
                if custom_name.strip() and actual_model.strip():
                    return (f"custom:{custom_name.strip()}", actual_model.strip())
            return (provider_part, model_part)
    return (current_provider, stripped)
""".lstrip(),
        encoding="utf-8",
    )
    calls = tmp_path / "calls.json"
    payload = {
        "provider": "studio",
        "model": "private-model",
        "providers": [
            {
                "slug": "openai-codex",
                "name": "OpenAI Codex",
                "models": ["gpt-5.6-sol", "gpt-5.5", "gpt-5.5"],
            },
            {
                "slug": "custom:lab",
                "name": "Lab",
                "models": ["vendor/model:fast"],
            },
            {
                "slug": "studio",
                "name": "Studio",
                "models": ["private-model"],
            },
        ],
    }
    environment = _isolated_environment(
        tmp_path,
        fake_hermes_package=fake_package,
        payload=payload,
        calls=calls,
    )

    first = subprocess.run(
        [sys.executable, str(door)],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    answer = json.loads(first.stdout)
    assert answer["status"] == "runnable"
    assert answer["defaultModelId"] == "custom:studio:private-model"
    assert [
        model["id"]
        for provider in answer["providers"]
        for model in provider["models"]
    ] == [
        "openai-codex:gpt-5.6-sol",
        "openai-codex:gpt-5.5",
        "custom:lab:vendor/model:fast",
        "custom:studio:private-model",
    ]
    selected = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from hermes_cli.models import parse_model_input;"
                "print(':'.join(parse_model_input("
                "'custom:studio:private-model', 'openai-codex')))"
            ),
        ],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert selected.stdout.strip() == "custom:studio:private-model"
    assert "provider status" in first.stderr
    first_call = json.loads(calls.read_text(encoding="utf-8"))
    assert first_call["explicit_only"] is True
    assert first_call["include_unconfigured"] is False
    assert first_call["refresh"] is False
    assert first_call["probe_custom_providers"] is False
    assert first_call["probe_current_custom_provider"] is True

    subprocess.run(
        [sys.executable, str(door), "--refresh"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    refresh_call = json.loads(calls.read_text(encoding="utf-8"))
    assert refresh_call["refresh"] is True
    assert refresh_call["probe_custom_providers"] is True
    assert refresh_call["probe_current_custom_provider"] is False
