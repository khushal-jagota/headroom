from __future__ import annotations

import pytest

from planner.cli import http
from planner.core import authctx
from planner.core.errors import ErrorCode, PlannerError


def test_request_identity_classifies_unattributed_chief_and_worker() -> None:
    unattributed = authctx._classify(None)
    chief = authctx._classify("chief")
    worker = authctx._classify("worker")

    assert (unattributed.actor, unattributed.is_attributed, unattributed.is_chief) == (
        "unattributed",
        False,
        False,
    )
    assert (chief.actor, chief.is_attributed, chief.is_chief) == ("chief", True, True)
    assert (worker.actor, worker.is_attributed, worker.is_chief) == ("worker", True, False)


def test_direct_write_accepts_unattributed_and_chief_but_rejects_other_actors() -> None:
    authctx.require_direct_write(authctx._classify(None))
    authctx.require_direct_write(authctx._classify("chief"))

    with pytest.raises(PlannerError) as raised:
        authctx.require_direct_write(authctx._classify("worker"))
    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail == {"actor": "worker"}


def test_chief_only_gate_requires_exact_chief_actor() -> None:
    authctx.require_chief(authctx._classify("chief"))

    for raw in (None, "worker", "agent", "human"):
        with pytest.raises(PlannerError) as raised:
            authctx.require_chief(authctx._classify(raw))
        assert raised.value.code is ErrorCode.agent_forbidden


def test_cli_header_modes_preserve_ambient_actor_without_privilege_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLAN_ACTOR", raising=False)
    assert http._headers("ordinary") == {}
    assert http._headers("worker") == {"X-Plan-Actor": "agent"}
    assert http._headers("chief") == {}

    monkeypatch.setenv("PLAN_ACTOR", "chief")
    assert http._headers("ordinary") == {"X-Plan-Actor": "chief"}
    assert http._headers("worker") == {"X-Plan-Actor": "chief"}
    assert http._headers("chief") == {"X-Plan-Actor": "chief"}

    monkeypatch.setenv("PLAN_ACTOR", "worker")
    assert http._headers("ordinary") == {"X-Plan-Actor": "worker"}
    assert http._headers("worker") == {"X-Plan-Actor": "worker"}
    assert http._headers("chief") == {"X-Plan-Actor": "worker"}
