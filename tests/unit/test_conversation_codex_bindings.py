"""The pin, and the parts of codex's protocol the adapter has hard-coded words for.

Codex's app-server negotiates nothing: no version handshake, no capability probe. A pinned
schema taken from the binary Panels spawns is the whole of the defence, so what is asserted
here is that the pin is intact and that the few strings this adapter writes by hand — the
answers to an approval, the sandbox it asks for — are still strings the pinned protocol
knows. When codex moves, these fail, which is the point.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server import generate_bindings as generator
from planner.conversation.backends.codex_app_server.adapter import (
    FULL_ACCESS_TURN_SANDBOX_POLICY,
    PERMISSION_ASK_OPTIONS,
    WITHDRAWN_ASK_DECISION,
)
from planner.conversation.backends.codex_app_server.generate_bindings import (
    GENERATED_BINDINGS_PATH,
    PINNED_CODEX_CLI_VERSION,
    VENDORED_SUBSET_SCHEMA_PATH,
)

CODEX_EXECUTABLE = shutil.which("codex")
PINNED_CODEX_AVAILABLE = (
    CODEX_EXECUTABLE is not None
    and generator._installed_codex_version(CODEX_EXECUTABLE) == PINNED_CODEX_CLI_VERSION
)
pinned_codex_only = pytest.mark.skipif(
    not PINNED_CODEX_AVAILABLE,
    reason="the pinned Codex binary is not installed",
)


def test_the_answers_this_offers_are_answers_codex_takes() -> None:
    """The options are codex's own words, passed back byte for byte."""
    for option in PERMISSION_ASK_OPTIONS:
        for model in (
            bindings.CommandExecutionRequestApprovalResponse,
            bindings.FileChangeRequestApprovalResponse,
        ):
            assert model.model_validate({"decision": option.option_id})


def test_the_answer_a_withdrawn_ask_gets_is_one_codex_takes() -> None:
    assert bindings.CommandExecutionRequestApprovalResponse.model_validate(
        {"decision": WITHDRAWN_ASK_DECISION}
    )
    assert bindings.FileChangeRequestApprovalResponse.model_validate(
        {"decision": WITHDRAWN_ASK_DECISION}
    )


def test_full_access_is_a_sandbox_the_pinned_protocol_knows() -> None:
    turn = bindings.TurnStartParams(
        threadId="t",
        input=[bindings.TextUserInput(type="text", text="hello")],
        approvalPolicy="never",
        approvalsReviewer="user",
        sandboxPolicy=FULL_ACCESS_TURN_SANDBOX_POLICY,
    )
    assert turn.sandboxPolicy == FULL_ACCESS_TURN_SANDBOX_POLICY
    assert bindings.ThreadStartParams(sandbox="danger-full-access").sandbox


@pinned_codex_only
def test_regeneration_from_the_pinned_binary_is_byte_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = CODEX_EXECUTABLE
    assert codex is not None
    with tempfile.TemporaryDirectory(
        prefix="binding-test-", dir=GENERATED_BINDINGS_PATH.parent
    ) as temporary_directory:
        generated = Path(temporary_directory) / "bindings_gen.py"
        schema = Path(temporary_directory) / "schema.json"
        monkeypatch.setattr(generator, "GENERATED_BINDINGS_PATH", generated)
        monkeypatch.setattr(generator, "VENDORED_SUBSET_SCHEMA_PATH", schema)
        generator.generate_bindings(codex_executable=codex)
        assert generated.read_bytes() == GENERATED_BINDINGS_PATH.read_bytes()
        assert schema.read_bytes() == VENDORED_SUBSET_SCHEMA_PATH.read_bytes()
