"""The actual Hermes interpreter retains extension import, lifecycle, and recovery compatibility."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tests.support.hermes_extension_proof import lifecycle_script, recovery_script

from planner.conversation.backends import hermes_acp
from planner.environments.hermes_home import resolve_hermes_python


@pytest.fixture(scope="module")
def extension_reports() -> list[dict[str, Any]]:
    interpreter = resolve_hermes_python()
    if not interpreter.is_file():
        pytest.skip("the configured Hermes interpreter is not installed")
    directory = Path(hermes_acp.__file__).resolve().parent
    script = (
        f"import sys; sys.path.insert(0, {str(directory)!r})\n"
        "import hermes_acp_extension as extension\n"
        "original_prompt = extension.hermes_server.HermesACPAgent.prompt\n"
        "original_cancel = extension.hermes_server.HermesACPAgent.cancel\n"
        f"exec({lifecycle_script(directory)!r}, {{}})\n"
        "extension.hermes_server.HermesACPAgent.prompt = original_prompt\n"
        "extension.hermes_server.HermesACPAgent.cancel = original_cancel\n"
        f"exec({recovery_script(directory)!r}, {{}})\n"
    )
    completed = subprocess.run(
        [str(interpreter), "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    reports = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(reports) == 2, completed.stdout
    return reports


def test_the_packaged_extension_owns_generation_lifecycle_and_redirect_admission(
    extension_reports: list[dict[str, Any]],
) -> None:
    report = extension_reports[0]
    token = {"conversationId": "c", "turnNumber": 7}
    assert report == {
        "accepted": {"accepted": True, "turnToken": token},
        "stale": {
            "accepted": False,
            "turnToken": {"conversationId": "c", "turnNumber": 6},
        },
        "redirect_false": {"accepted": False, "turnToken": token},
        "concurrent_steer": {
            "accepted": False,
            "turnToken": {"conversationId": "c", "turnNumber": 8},
        },
        "native": {"accepted": False, "turnToken": token},
        "after_cancel": {"accepted": False, "turnToken": token},
        "after_completion": {"accepted": False, "turnToken": token},
        "redirects": ["new direction", "not accepted"],
        "queued": ["existing"],
    }


def test_the_packaged_extension_recovers_only_an_owned_null_final_response(
    extension_reports: list[dict[str, Any]],
) -> None:
    assert extension_reports[1] == {
        "owned": ["cancelled", False, ""],
        "unrelated": ["persistence failed", True, "owned"],
        "non_owning": [
            "'NoneType' object has no attribute 'startswith'",
            True,
            "incumbent",
            {"accepted": True, "turnToken": {"conversationId": "c", "turnNumber": 1}},
        ],
    }
