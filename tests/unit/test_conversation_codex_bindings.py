"""The pin, and the parts of codex's protocol the adapter has hard-coded words for.

Codex's app-server negotiates nothing: no version handshake, no capability probe. A pinned
schema taken from the binary Panels spawns is the whole of the defence, so what is asserted
here is that the pin is intact and that the few strings this adapter writes by hand — the
answers to an approval, the sandbox it asks for — are still strings the pinned protocol
knows. When codex moves, these fail, which is the point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.adapter import (
    FULL_ACCESS_TURN_SANDBOX_POLICY,
    PERMISSION_ASK_OPTIONS,
    WITHDRAWN_ASK_DECISION,
)
from planner.conversation.backends.codex_app_server.generate_bindings import (
    GENERATED_BINDINGS_PATH,
    PINNED_CODEX_CLI_VERSION,
    PINNED_UPSTREAM_COMMIT,
    PINNED_UPSTREAM_TAG,
    SCHEMA_ROOTS,
    VENDORED_SUBSET_SCHEMA_PATH,
)


def test_the_generated_bindings_say_which_codex_they_came_from() -> None:
    """The pin is written where a reader of the models will see it."""
    header = GENERATED_BINDINGS_PATH.read_text(encoding="utf-8")[:2000]
    assert PINNED_CODEX_CLI_VERSION in header
    assert PINNED_UPSTREAM_TAG in header
    assert PINNED_UPSTREAM_COMMIT in header
    assert "codex app-server generate-json-schema" in header


def test_the_vendored_schema_holds_every_message_the_adapter_speaks() -> None:
    vendored = json.loads(VENDORED_SUBSET_SCHEMA_PATH.read_text(encoding="utf-8"))
    for _namespace, name in SCHEMA_ROOTS:
        assert name in vendored["definitions"], f"{name} is not in the vendored schema"


def test_the_vendored_schema_is_a_document_that_can_stand_on_its_own() -> None:
    """Every reference in it points at something in it, so it regenerates without codex."""
    vendored = json.loads(VENDORED_SUBSET_SCHEMA_PATH.read_text(encoding="utf-8"))
    definitions = vendored["definitions"]
    text = VENDORED_SUBSET_SCHEMA_PATH.read_text(encoding="utf-8")
    for reference in _references_in(json.loads(text)):
        assert reference in definitions, f"{reference} is referenced but not defined"


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


def test_an_answer_codex_does_not_offer_is_refused_here_not_on_the_wire() -> None:
    with pytest.raises(ValidationError):
        bindings.CommandExecutionRequestApprovalResponse.model_validate({"decision": "yes"})


def test_user_input_request_and_complete_answer_map_match_the_pinned_protocol() -> None:
    request = bindings.ToolRequestUserInputParams.model_validate(
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "isBlocking": True,
            "questions": [
                {
                    "id": "framework",
                    "header": "Framework",
                    "question": "Which framework?",
                    "options": [
                        {"label": "Svelte", "description": "Use Svelte components"},
                        {"label": "React", "description": "Use React components"},
                    ],
                    "isOther": True,
                    "isSecret": False,
                },
                {
                    "id": "notes",
                    "header": "Notes",
                    "question": "Anything else?",
                    "options": None,
                    "isOther": True,
                    "isSecret": False,
                },
            ],
        }
    )
    assert [question.id for question in request.questions] == ["framework", "notes"]
    assert request.questions[0].options is not None
    assert request.questions[0].options[0].description == "Use Svelte components"

    answer = bindings.ToolRequestUserInputResponse.model_validate(
        {
            "answers": {
                "framework": {"answers": ["Svelte"]},
                "notes": {"answers": ["Keep it compact"]},
            }
        }
    )
    assert answer.model_dump(mode="json", exclude_none=True) == {
        "answers": {
            "framework": {"answers": ["Svelte"]},
            "notes": {"answers": ["Keep it compact"]},
        }
    }


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


def _references_in(document: object) -> list[str]:
    found: list[str] = []
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str):
                found.append(reference.removeprefix("#/definitions/"))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return found


def test_the_vendored_schema_sits_next_to_the_models_it_generated() -> None:
    assert VENDORED_SUBSET_SCHEMA_PATH.parent.parent == Path(bindings.__file__).parent
