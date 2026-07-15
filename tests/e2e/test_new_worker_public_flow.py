"""Focused public-flow coverage for the shipped new_worker Understanding stage."""

from __future__ import annotations

import time

import httpx
from tests.e2e.conftest import FAKE_NOW, WAIT_MS

from planner.core.clock import TestClock as MutableClock
from planner.core.clock import parse_fake_now
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.automatic_employee_step_eligibility_wake import (
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.runtime.employee_step_runner import EmployeeStepRunner


def _run_automatic_opening(server, ticket_id: str) -> FakeGateway:
    """Run the production opening path explicitly; test-mode servers omit all loops."""
    fake = FakeGateway(
        {
            "session.create": [
                Reply(
                    result={
                        "session_id": "opening-live-session",
                        "stored_session_id": "fake-sess-1",
                    }
                )
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        ev(
                            "message.complete",
                            "opening-live-session",
                            {
                                "text": "What should this worker understand before we design it?",
                                "usage": {},
                                "status": "complete",
                            },
                        ),
                    ),
                )
            ],
        }
    )
    gateway = SharedGateway(
        hermes_python="python",
        home=str(server.db_path.parent / "automatic-opening-home"),
        worker_role="planning-worker",
        spawn=fake.spawn,
        base_env={},
    )
    runner = EmployeeStepRunner(
        str(server.db_path),
        MutableClock(parse_fake_now(FAKE_NOW)),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=5,
    )
    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(10.0)
    runner.stop()
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    return fake


def _post_chat(server, ticket_id: str, text: str) -> dict:
    response = httpx.post(
        f"{server.base}/api/chat/{ticket_id}/turns",
        json={"text": text, "mode": "message"},
        timeout=10.0,
    )
    assert response.status_code < 300, response.text
    return response.json()


def _wait_for_ticket(api, server, ticket_id: str, predicate) -> dict:
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        last = api.get(server, f"/api/tickets/{ticket_id}")
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"ticket condition was not met; last={last!r}")


def _wait_for_chat_turn_complete(api, server, ticket_id: str, turn_id: str) -> dict:
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        last = api.get(server, f"/api/chat/{ticket_id}/state")
        has_assistant_message = any(
            message["role"] == "assistant" and message["turn_id"] == turn_id
            for message in last["messages"]
        )
        if last["active_turn"] is None and has_assistant_message:
            return last
        time.sleep(0.05)
    raise AssertionError(f"chat turn {turn_id} did not complete; last={last!r}")


def test_new_worker_understanding_public_chat_reuses_session_and_proposal_parks(
    server, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "new_worker",
        "--title",
        "Design a public API worker",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    _run_automatic_opening(server, ticket_id)

    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage"] == "needs_understanding"
    assert detail["employee_session_id"] == "fake-sess-1"
    assert detail["ticket_status"] == "paired_work"
    first = detail
    assert first["ticket_status"] == "paired_work"
    assert first["fields"]["understanding"]["proposal"] is None

    second_turn = _post_chat(server, ticket_id, "Continue the Understanding conversation.")
    _wait_for_chat_turn_complete(api, server, ticket_id, second_turn["id"])
    second = api.get(server, f"/api/tickets/{ticket_id}")
    assert second["employee_session_id"] == first["employee_session_id"]
    assert second["ticket_status"] == "paired_work"
    assert second["fields"]["understanding"]["proposal"] is None

    proposed = cli(
        server,
        "worker",
        "propose",
        ticket_id,
        "--body-file",
        "-",
        "--recap",
        "Understanding ready.",
        stdin="Purpose/outcome, judgment risks, and boundaries captured.",
    )
    assert proposed["stage"] == "needs_understanding"
    assert proposed["ticket_status"] == "awaiting_approval"
    assert proposed["fields"]["understanding"]["proposal"]["body"].startswith(
        "Purpose/outcome"
    )

    approved = api.direct_post(
        server,
        f"/api/tickets/{ticket_id}/accept/understanding",
        {"next_ceiling": "needs_stages", "at_cap": "propose"},
    )
    assert approved["stage"] == "needs_stages"
    assert approved["fields"]["understanding"]["proposal"] is None
    assert approved["fields"]["understanding"]["value"].startswith("Purpose/outcome")


def test_new_worker_understanding_public_chat_proposal_and_browser_progression(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "new_worker",
        "--title",
        "Design a research worker",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    _run_automatic_opening(server, ticket_id)

    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage"] == "needs_understanding"
    assert detail["default_stage_ownership_mode"] == "paired"
    assert detail["effective_stage_ownership_mode"] == "paired"
    assert detail["employee_session_id"] == "fake-sess-1"
    assert detail["ticket_status"] == "paired_work"
    assert detail["fields"]["understanding"]["proposal"] is None

    second_turn = _post_chat(server, ticket_id, "Continue the Understanding conversation.")
    _wait_for_chat_turn_complete(api, server, ticket_id, second_turn["id"])
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["employee_session_id"] == "fake-sess-1"
    assert detail["ticket_status"] == "paired_work"

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"][data-stage="needs_understanding"]',
        settled=True,
    )
    assert page.eval_on_selector_all(
        "details[data-field]",
        "els => els.map(el => el.getAttribute('data-field'))",
    ) == ["kickoff", "understanding", "stages", "thinking", "drafting", "closeout"]
    assert (
        page.locator(".ticket-facts [data-stage-owner]").get_attribute("data-default-owner")
        == "paired"
    )
    assert (
        page.locator(".ticket-facts [data-stage-owner]").get_attribute("data-effective-owner")
        == "paired"
    )
    assert (
        page.locator('details[data-field="understanding"]').get_attribute("data-stage-state")
        == "current-paired-work"
    )

    cli(
        server,
        "worker",
        "propose",
        ticket_id,
        "--body-file",
        "-",
        "--recap",
        "Understanding ready.",
        stdin=(
            "Purpose/outcome: design a research worker.\n"
            "Risks/judgment: source trust and stop criteria need human review.\n"
            "Constraints/examples/boundaries: cite sources; do not browse without need."
        ),
    )
    detail = _wait_for_ticket(
        api,
        server,
        ticket_id,
        lambda ticket: ticket["fields"]["understanding"]["proposal"] is not None,
    )
    assert detail["stage"] == "needs_understanding"
    assert detail["ticket_status"] == "awaiting_approval"

    page.wait_for_selector(
        'details[data-field="understanding"] [data-approval-block][data-mode="gating-pending"]',
        timeout=WAIT_MS,
    )
    page.locator('details[data-field="understanding"] [data-accept]').click()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"][data-stage="needs_stages"]',
        timeout=WAIT_MS,
    )

    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["stage"] == "needs_stages"
    assert detail["fields"]["understanding"]["proposal"] is None
    assert "Purpose/outcome" in detail["fields"]["understanding"]["value"]
