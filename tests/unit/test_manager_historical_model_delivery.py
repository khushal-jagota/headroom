"""Stale Manager launch choices must not block a healthy linked conversation.

The first-model fixture already runs the real conversation system and Codex adapter.
Reuse it to prove owner messages, durable wakes and review notices on one conversation.
"""

from __future__ import annotations

import json
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.unit.test_manager_first_model import _codex_app, _turns
from tests.unit.test_review_transfer import _parked_ticket, _transfer
from tests.unit.test_sprint_item_supervisors import _create_item

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.manager_wakes import data as wake_data
from planner.manager_wakes.runtime import deliver_batch, reconcile_batch_outcomes
from planner.tickets import data as tickets_data
from planner.tickets.review_transfer import notify_review_transfers


def _wait_finished(db_path: Path, conversation_id: str, count: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with closing(connect(str(db_path))) as conn:
            actual = conn.execute(
                "SELECT count(*) FROM conversation_events "
                "WHERE conversation_id=? AND kind='turn_ended'",
                (conversation_id,),
            ).fetchone()[0]
        if actual >= count:
            return
        time.sleep(0.01)
    pytest.fail(f"Expected {count} finished turns in the same Manager conversation")


@pytest.mark.parametrize("historical_model", ["sol", "gpt-5.6-sol"])
def test_historical_saved_choice_does_not_block_existing_manager_delivery(
    tmp_path: Path,
    fake_clock: FakeClock,
    historical_model: str,
) -> None:
    app, db_path, transcript = _codex_app(tmp_path)
    # The scripted provider completes each successive turn without a special response.
    with TestClient(app) as client:
        portal = client.portal
        assert portal is not None
        manager = _create_item(client)
        address = f"/api/managers/{manager['id']}/conversation/send"
        first = client.post(
            address,
            json={
                "content": [{"piece": "text", "text": "Start on the valid concrete model."}],
                "sender_label": "owner",
            },
        )
        assert first.status_code == 200, first.text
        conversation_id = first.json()["conversation_id"]
        _wait_finished(db_path, conversation_id, 1)
        with closing(connect(str(db_path))) as conn:
            conn.execute(
                "UPDATE managers SET manager_model=? WHERE id=?",
                (
                    historical_model,
                    manager["id"],
                ),
            )
            if historical_model == "gpt-5.6-sol":
                write_model_enablement(
                    conn, ConversationBackendKey.codex, historical_model, enabled=False
                )
            conn.commit()
        start_values = client.get(f"/api/managers/{manager['id']}/conversation/start-values")
        assert start_values.status_code == 200, start_values.text
        assert start_values.json()["model"] == historical_model
        second = client.post(
            address,
            json={
                "content": [{"piece": "text", "text": "Continue the healthy conversation."}],
                "sender_label": "owner",
            },
        )
        assert second.status_code == 200, second.text
        assert second.json()["conversation_id"] == conversation_id
        _wait_finished(db_path, conversation_id, 2)

        async def notifications() -> None:
            system = app.state.conversation_system
            with closing(connect(str(db_path))) as conn:
                ticket_id = _parked_ticket(
                    conn,
                    fake_clock,
                    title="Transferred review",
                    holder=OWNER_PRINCIPAL,
                    manager_id=manager["id"],
                )
                wake_data.create_worker_error_wake(
                    conn,
                    target=Principal(PrincipalKind.manager, manager["id"]),
                    ticket_id=ticket_id,
                    claim_revision=1,
                    ticket_title="Transferred review",
                    error_reason="delivery uncertain",
                    now=1,
                )
                conn.commit()
                batch = wake_data.claim_next_batch(conn, process_token="test", now=2)
                assert batch is not None
                assert batch.target.id == manager["id"]
                conn.commit()
            await deliver_batch(
                batch,
                connect_database=lambda: connect(str(db_path)),
                conversation_system=system,
                process_token="test",
                now=lambda: 3,
            )

        portal.call(notifications)
        _wait_finished(db_path, conversation_id, 3)
        with closing(connect(str(db_path))) as conn:
            reconcile_batch_outcomes(conn, now=4)
            batch_row = conn.execute("SELECT status FROM manager_wake_batches").fetchone()
            assert batch_row[0] == "delivered"
            ticket_id = conn.execute(
                "SELECT id FROM tickets WHERE title='Transferred review'"
            ).fetchone()[0]

        async def transfer() -> None:
            with closing(connect(str(db_path))) as conn:
                target = Principal(PrincipalKind.manager, manager["id"])
                notice = _transfer(conn, fake_clock, ticket_id, new_holder=target, actor=target)
                await notify_review_transfers(
                    app.state.conversation_system, conn, [notice], now=fake_clock.now_unix()
                )

        portal.call(transfer)
        _wait_finished(db_path, conversation_id, 4)
        turns = _turns(transcript)
        assert len(turns) == 4
        assert {turn["params"]["model"] for turn in turns} == {"gpt-6.1-sol"}
        with closing(connect(str(db_path))) as conn:
            prompts = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload FROM conversation_events WHERE conversation_id=? "
                    "AND kind='prompt' ORDER BY sequence",
                    (conversation_id,),
                )
            ]
            assert len(prompts) == 4
            assert "You now hold the review" in json.dumps(prompts[-1])
            assert (
                conn.execute(
                    "SELECT manager_model FROM managers WHERE id=?", (manager["id"],)
                ).fetchone()[0]
                == historical_model
            )
        # A disabled actual conversation model still follows the global durable refusal policy.
        with closing(connect(str(db_path))) as conn:
            write_model_enablement(conn, ConversationBackendKey.codex, "gpt-6.1-sol", enabled=False)
        disabled = client.post(
            address,
            json={
                "content": [{"piece": "text", "text": "This must receive a durable refusal."}],
                "sender_label": "owner",
            },
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["fate"] == "refused"
        assert disabled.json()["refusal_reason"] == "model_resolution_failed"
        assert len(_turns(transcript)) == 4
        with closing(connect(str(db_path))) as conn:
            refusal = conn.execute(
                "SELECT payload FROM conversation_events WHERE conversation_id=? "
                "AND kind='prompt_delivery_refused' ORDER BY sequence DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            assert refusal is not None
            assert "disabled" in refusal[0].lower()


@pytest.mark.parametrize("historical_model", ["sol", "gpt-5.6-sol"])
def test_cold_invalid_manager_wake_refuses_durably_then_repairs_without_blocking_other_target(
    tmp_path: Path,
    fake_clock: FakeClock,
    historical_model: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed start must retire its attempt, leave retry possible, and not poison the poll."""
    import asyncio

    from planner.manager_wakes.runtime import ManagerWakeLoop

    app, db_path, transcript = _codex_app(tmp_path)
    with TestClient(app) as client:
        portal = client.portal
        assert portal is not None
        invalid = _create_item(client, "Historical invalid start")
        healthy = _create_item(client, "Other target still receives work")
        with closing(connect(str(db_path))) as conn:
            conn.execute(
                "UPDATE managers SET manager_model=? WHERE id=?",
                (
                    historical_model,
                    invalid["id"],
                ),
            )
            if historical_model == "gpt-5.6-sol":
                write_model_enablement(
                    conn, ConversationBackendKey.codex, historical_model, enabled=False
                )
            for manager in (invalid, healthy):
                ticket_id = _parked_ticket(
                    conn,
                    fake_clock,
                    title=manager["title"],
                    holder=OWNER_PRINCIPAL,
                    manager_id=manager["id"],
                )
                wake_data.create_worker_error_wake(
                    conn,
                    target=Principal(PrincipalKind.manager, manager["id"]),
                    ticket_id=ticket_id,
                    claim_revision=1,
                    ticket_title=manager["title"],
                    error_reason="delivery uncertain",
                    now=1,
                )
            conn.commit()

        async def active_loop() -> asyncio.AbstractEventLoop:
            return asyncio.get_running_loop()

        wake_loop = ManagerWakeLoop(
            str(db_path),
            fake_clock,
            conversation_system=app.state.conversation_system,
            asyncio_loop=portal.call(active_loop),
        )

        def outcomes() -> dict[str, str]:
            with closing(connect(str(db_path))) as conn:
                reconcile_batch_outcomes(conn, now=5)
                return dict(conn.execute("SELECT target_id,status FROM manager_wake_batches"))

        try:
            assert len(wake_loop.poll_once()) == 2
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                statuses = outcomes()
                if (
                    statuses.get(invalid["id"]) == "refused"
                    and statuses.get(healthy["id"]) == "delivered"
                ):
                    break
                time.sleep(0.01)
            else:
                pytest.fail(
                    f"Invalid target stayed pending or blocked another target: {outcomes()}"
                )
            assert len(_turns(transcript)) == 1
            assert "manager wake delivery ended in an uncertain failure" not in caplog.text

            async def transfer_to_invalid() -> None:
                with closing(connect(str(db_path))) as conn:
                    ticket = conn.execute(
                        "SELECT id FROM tickets WHERE title=?", (invalid["title"],)
                    ).fetchone()[0]
                    target = Principal(PrincipalKind.manager, invalid["id"])
                    notice = _transfer(conn, fake_clock, ticket, new_holder=target, actor=target)
                    await notify_review_transfers(
                        app.state.conversation_system, conn, [notice], now=fake_clock.now_unix()
                    )
                    assert tickets_data.read_ticket(conn, ticket).ceiling_holder == target

            portal.call(transfer_to_invalid)
            assert "review transfer notice failed" not in caplog.text
            assert len(_turns(transcript)) == 1

            repaired = client.put(
                f"/api/managers/{invalid['id']}/launch-defaults",
                json={
                    "employee_backend": "codex",
                    "employee_launch_model": "gpt-6.1-sol",
                    "employee_launch_reasoning_effort": "medium",
                },
            )
            assert repaired.status_code == 200, repaired.text
            assert wake_loop.poll_once()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if outcomes().get(invalid["id"]) == "delivered":
                    break
                time.sleep(0.01)
            else:
                pytest.fail(f"Repaired Manager wake could not retry: {outcomes()}")
            assert len(_turns(transcript)) == 2
        finally:
            wake_loop.stop()


def test_explicit_saved_choice_reaches_wake_and_review_without_owner_message(
    tmp_path: Path,
    fake_clock: FakeClock,
) -> None:
    """Settings save must govern non-owner delivery without rewriting historical choices."""
    app, db_path, transcript = _codex_app(tmp_path)
    with TestClient(app) as client:
        portal = client.portal
        assert portal is not None
        manager = _create_item(client)
        first = client.post(
            f"/api/managers/{manager['id']}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Initialize the conversation."}],
                "sender_label": "owner",
            },
        )
        assert first.status_code == 200, first.text
        conversation_id = first.json()["conversation_id"]
        _wait_finished(db_path, conversation_id, 1)

        def save(model: str) -> None:
            saved = client.put(
                f"/api/managers/{manager['id']}/launch-defaults",
                json={
                    "employee_backend": "codex",
                    "employee_launch_model": model,
                    "employee_launch_reasoning_effort": "medium",
                },
            )
            assert saved.status_code == 200, saved.text

        save("gpt-6-astra")

        async def wake() -> str:
            with closing(connect(str(db_path))) as conn:
                ticket_id = _parked_ticket(
                    conn,
                    fake_clock,
                    title="New review",
                    holder=OWNER_PRINCIPAL,
                    manager_id=manager["id"],
                )
                wake_data.create_worker_error_wake(
                    conn,
                    target=Principal(PrincipalKind.manager, manager["id"]),
                    ticket_id=ticket_id,
                    claim_revision=1,
                    ticket_title="New review",
                    error_reason="delivery uncertain",
                    now=1,
                )
                conn.commit()
                batch = wake_data.claim_next_batch(conn, process_token="test", now=2)
                assert batch is not None
                conn.commit()
            await deliver_batch(
                batch,
                connect_database=lambda: connect(str(db_path)),
                conversation_system=app.state.conversation_system,
                process_token="test",
                now=lambda: 3,
            )
            return ticket_id

        ticket_id = portal.call(wake)
        _wait_finished(db_path, conversation_id, 2)
        save("gpt-6.1-terra")

        async def transfer() -> None:
            with closing(connect(str(db_path))) as conn:
                target = Principal(PrincipalKind.manager, manager["id"])
                notice = _transfer(conn, fake_clock, ticket_id, new_holder=target, actor=target)
                await notify_review_transfers(
                    app.state.conversation_system, conn, [notice], now=fake_clock.now_unix()
                )

        portal.call(transfer)
        _wait_finished(db_path, conversation_id, 3)
        assert [turn["params"]["model"] for turn in _turns(transcript)] == [
            "gpt-6.1-sol",
            "gpt-6-astra",
            "gpt-6.1-terra",
        ]
        with closing(connect(str(db_path))) as conn:
            assert (
                conn.execute(
                    "SELECT conversation_id FROM agents WHERE agent_key=?",
                    (f"manager_{manager['id']}",),
                ).fetchone()[0]
                == conversation_id
            )
