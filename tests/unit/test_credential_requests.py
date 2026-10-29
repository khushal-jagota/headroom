"""Request resolution, private entry and actual conversation delivery share real storage.

Reuse the existing conversation HTTP host and adapter. New cases protect boundaries its
turn-bound input tests do not cover: durable independent cards, atomic credential writes,
origin binding, and result delivery whose retry must never repeat the secret write.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from tests.support.model_catalogue import seed_test_model_catalogue
from tests.unit.test_conversation_api import _Harness
from tests.unit.test_conversation_system import _link_test_ticket_to_conversation

from planner.conversation.backends.contracts import BackendSteerRefused
from planner.conversation.contracts import ConversationStartRequest, PromptDeliveryRefusalReason
from planner.conversation.events import CredentialRequestEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.conversation.views.storage import read_row
from planner.core.authctx import RequestContext
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import (
    OWNER_PRINCIPAL,
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.core.server import handle_planner_error
from planner.credentials import actions as credentials
from planner.credentials import data as credential_data
from planner.credentials.contracts import (
    BearerSecret,
    CredentialAuthKind,
    CredentialReference,
    CredentialScheme,
    CredentialWrite,
)
from planner.credentials.requests import actions, api, data
from planner.runtime.logic.conversation_start_resolution import worker_conversation_role_materials

SECRET = "request-secret-never-in-transcript-2834"
SECOND = "request-fresh-secret-7463"
HEADERS = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_failed", "X-Plan-Conversation-ID": "c"}


def request_body(name: str = "Panels_posthog_production", **changes: object) -> dict[str, object]:
    return {
        "request_id": str(uuid4()),
        "name": name,
        "provider": "PostHog",
        "use": "Read project metrics",
        "auth_kind": "bearer",
        "header_name": None,
        "help": "Create a read key",
        "help_url": "https://example.com/keys",
        "replacement": None,
        **changes,
    }


def build(db: Path, *, signal: SignalRuntimeComposition | None = None) -> _Harness:
    host = _Harness(
        db,
        signal_composition=signal,
        store_options={
            "event_participant": data.observe_events,
            "startup_participant": data.reconcile_deliveries,
        },
    )
    cfg = load_config(
        None,
        env={
            "PLAN_DB_PATH": str(db),
            "PLAN_TEST_MODE": "1",
            "PLAN_CREDENTIAL_KEY_PATH": str(db.parent / "key"),
        },
    )
    host.app.state.config = cfg
    host.app.state.clock = build_clock(cfg)
    host.app.state.conn_factory = lambda: connect(str(db))
    host.app.include_router(api.router, prefix="/api")
    host.app.add_exception_handler(PlannerError, handle_planner_error)
    return host


@pytest.fixture
def host(tmp_path: Path) -> Iterator[_Harness]:
    db = tmp_path / "requests.db"
    conn = connect(str(db))
    create_schema(conn)
    seed_test_model_catalogue(conn)
    conn.close()
    built = build(db)
    yield built
    asyncio.run(built.runtime.shutdown())


async def start(host: _Harness, *, busy: bool = False) -> None:
    await host.system.start_conversation(
        ConversationStartRequest(
            conversation_id="c",
            model="gpt-6-sol",
            role_materials=worker_conversation_role_materials("t_failed"),
            workspace_folder=host.db_path.parent,
        )
    )
    _link_test_ticket_to_conversation(host.db_path, "c")
    if busy:
        await host.system.send(
            "c", text_message_content("Work until the current turn ends."), sender_label="owner"
        )


def saved_write(name: str) -> CredentialWrite:
    return CredentialWrite(
        CredentialReference(CredentialScheme.local, name),
        CredentialAuthKind.bearer,
        BearerSecret(SECRET),
    )


def test_request_outlives_turn_and_concurrent_resolution_is_one_private_write(
    host: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        await start(host, busy=True)
        body = request_body()
        async with host.client() as client:
            created = await client.post("/api/credentials/requests", json=body, headers=HEADERS)
            assert created.status_code == 200, created.text
            await host.complete_turn("c")
            request_id = str(body["request_id"])
            url = f"/api/credentials/requests/{request_id}"
            assert (await client.get(url)).json()["state"] == "pending"
            denied = await client.post(
                url + "/save",
                json={"name": body["name"], "value": SECRET, "expected_revision": None},
                headers=HEADERS,
            )
            assert denied.json()["error"]["code"] == "agent_forbidden"
            assert SECRET not in denied.text
            responses = await asyncio.gather(
                *[
                    client.post(
                        url + "/save",
                        json={"name": body["name"], "value": SECRET, "expected_revision": None},
                    )
                    for _ in range(2)
                ],
                client.post(url + "/reject"),
            )
            assert all(response.status_code == 200 for response in responses)
            result = (await client.get(url)).json()
            assert result["state"] in {"saved", "rejected"}
            assert result["delivery"]["status"] == "started"
            replay = await client.post("/api/credentials/requests", json=body, headers=HEADERS)
            assert replay.json() == result
            changed = await client.post(
                "/api/credentials/requests", json={**body, "use": "Different work"}, headers=HEADERS
            )
            assert changed.status_code == 400
            assert SECRET not in changed.text
        conn = connect(str(host.db_path))
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == (
            result["state"] == "saved"
        )
        assert (
            conn.execute("SELECT count(*) FROM credential_request_delivery_attempts").fetchone()[0]
            == 1
        )
        source = conn.execute(
            "SELECT sequence FROM conversation_view_rows WHERE row_kind='credential_request'"
        ).fetchall()
        assert len(source) == 1
        row, _ = read_row(conn, "c", source[0][0], stopped=True)
        assert row["body"]["kind"] == "inline"
        assert row["body"]["value"] == result
        transcript = json.dumps(
            [tuple(row) for row in conn.execute("SELECT kind,payload FROM conversation_events")]
        )
        assert SECRET not in transcript and SECRET not in caplog.text
        assert len(host.backend("c").written_texts) == 2
        assert request_id in host.backend("c").written_texts[-1]
        assert result["state"] in host.backend("c").written_texts[-1]
        assert SECRET not in host.backend("c").written_texts[-1]
        conn.close()

    asyncio.run(exercise())


def test_replacement_conflict_requires_review_and_atomic_failure_keeps_request_pending(
    host: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        await start(host)
        conn = connect(str(host.db_path))
        key = Path(host.app.state.config.credential_key_path)
        metadata = credentials.create_credential(
            conn, saved_write("replace_me"), principal=OWNER_PRINCIPAL, now=1, key_path=key
        )
        body = request_body(
            "replace_me",
            replacement={
                "credential_id": metadata.id,
                "name": metadata.reference.name,
                "revision": 1,
            },
        )
        async with host.client() as client:
            created = await client.post("/api/credentials/requests", json=body, headers=HEADERS)
            assert created.status_code == 200, created.text
            credentials.replace_credential(
                conn,
                metadata.id,
                BearerSecret(SECOND),
                expected_revision=1,
                principal=OWNER_PRINCIPAL,
                now=2,
                key_path=key,
            )
            url = f"/api/credentials/requests/{body['request_id']}"
            stale = await client.post(
                url + "/save", json={"name": "replace_me", "value": SECRET, "expected_revision": 1}
            )
            assert stale.status_code == 409, stale.text
            assert stale.json()["error"]["detail"]["current_credential"]["revision"] == 2
            assert SECRET not in stale.text
            reviewed = await client.post(url + "/review")
            assert reviewed.json()["replacement"]["revision"] == 2
            original = credentials.replace_credential_in_transaction

            def fail_after_write(*args, **kwargs):
                original(*args, **kwargs)
                raise PlannerError(ErrorCode.validation, "synthetic storage failure")

            with monkeypatch.context() as patch:
                patch.setattr(credentials, "replace_credential_in_transaction", fail_after_write)
                failed = await client.post(
                    url + "/save",
                    json={"name": "replace_me", "value": SECRET, "expected_revision": 2},
                )
                assert failed.status_code == 400
            assert (await client.get(url)).json()["state"] == "pending"
            assert credential_data.read_metadata(conn, metadata.id).revision == 2
            assert (
                conn.execute(
                    "SELECT count(*) FROM credential_request_delivery_attempts"
                ).fetchone()[0]
                == 0
            )
            fixed = await client.post(
                url + "/save", json={"name": "other_name", "value": SECOND, "expected_revision": 2}
            )
            assert fixed.status_code == 400
            done = await client.post(
                url + "/save", json={"name": "replace_me", "value": SECOND, "expected_revision": 2}
            )
            assert done.json()["state"] == "replaced"
            assert done.json()["credential"]["revision"] == 3
            assert credential_data.read_metadata(conn, metadata.id).revision == 3
        conn.close()

    asyncio.run(exercise())


def test_reuse_queue_drain_and_discard_update_original_card(host: _Harness) -> None:
    async def exercise() -> None:
        await start(host, busy=True)
        host.backend("c").steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.backend_rejected_steer
        )
        conn = connect(str(host.db_path))
        credentials.create_credential(
            conn,
            saved_write("reuse_me"),
            principal=OWNER_PRINCIPAL,
            now=1,
            key_path=Path(host.app.state.config.credential_key_path),
        )
        async with host.client() as client:
            first = request_body("reuse_me")
            response = await client.post("/api/credentials/requests", json=first, headers=HEADERS)
            assert response.json()["state"] == "reused"
            assert response.json()["delivery"]["status"] == "queued"
            retry = await client.post(
                f"/api/credentials/requests/{first['request_id']}/retry-delivery"
            )
            assert retry.json() == response.json()
            assert len(await host.system.held_prompts("c")) == 1
            await host.complete_turn("c")
            delivered = (
                await client.get(f"/api/credentials/requests/{first['request_id']}")
            ).json()
            assert delivered["delivery"]["status"] == "started"
            second = request_body("reuse_me")
            again = await client.post("/api/credentials/requests", json=second, headers=HEADERS)
            assert again.json()["delivery"]["status"] == "queued"
            held = await host.system.held_prompts("c")
            assert await host.system.discard_held_prompt("c", held[0].held_prompt_id)
            canceled = (
                await client.get(f"/api/credentials/requests/{second['request_id']}")
            ).json()
            assert canceled["delivery"]["status"] == "canceled"
            rows = conn.execute(
                "SELECT sequence FROM conversation_view_rows WHERE row_kind='credential_request' "
                "ORDER BY sequence"
            ).fetchall()
            assert len(rows) == 2
            for sequence, state in zip(rows, [delivered, canceled], strict=True):
                projected, _ = read_row(conn, "c", sequence[0])
                assert projected["body"]["kind"] == "inline" and projected["body"]["value"] == state
            assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 1
        conn.close()

    asyncio.run(exercise())


def test_only_proven_refusal_retries_delivery_without_another_save(host: _Harness) -> None:
    async def exercise() -> None:
        await start(host)
        async with host.client() as client:
            body = request_body()
            assert (
                await client.post("/api/credentials/requests", json=body, headers=HEADERS)
            ).status_code == 200
            url = f"/api/credentials/requests/{body['request_id']}"
            host.backend("c").spawn_fails = True
            refused = await client.post(url + "/reject")
            assert refused.json()["delivery"]["status"] == "refused", refused.text
            old_attempt = refused.json()["delivery"]["attempt_id"]
            host.backend("c").spawn_fails = False
            replies = await asyncio.gather(
                client.post(url + "/retry-delivery"), client.post(url + "/retry-delivery")
            )
            assert all(
                reply.json()["delivery"]["status"] in {"pending", "started"} for reply in replies
            )
            result = (await client.get(url)).json()
            assert result["delivery"]["attempt_id"] != old_attempt
            assert result["delivery"]["status"] == "started"
            assert len(host.backend("c").written_texts) == 1
            await host.complete_turn("c")
            second = request_body()
            await client.post("/api/credentials/requests", json=second, headers=HEADERS)
            host.backend("c").write_fails = True
            uncertain_url = f"/api/credentials/requests/{second['request_id']}"
            uncertain = await client.post(uncertain_url + "/reject")
            assert uncertain.json()["delivery"]["status"] == "uncertain", uncertain.text
            unchanged = await client.post(uncertain_url + "/retry-delivery")
            assert unchanged.json() == uncertain.json()
        conn = connect(str(host.db_path))
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM credential_request_delivery_attempts").fetchone()[0]
            == 3
        )
        conn.close()

    asyncio.run(exercise())


def test_lost_intent_and_lost_queue_restart_as_uncertain_without_a_send(host: _Harness) -> None:
    async def exercise() -> None:
        await start(host, busy=True)
        host.backend("c").steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.backend_rejected_steer
        )
        async with host.client() as client:
            queued = request_body()
            await client.post("/api/credentials/requests", json=queued, headers=HEADERS)
            await client.post(f"/api/credentials/requests/{queued['request_id']}/reject")
            intent = request_body()
            await client.post("/api/credentials/requests", json=intent, headers=HEADERS)
            await host.system.record_events_transaction(
                "c",
                lambda conn: actions.reject(
                    conn, str(intent["request_id"]), principal=OWNER_PRINCIPAL, now=5
                ),
            )
        await host.runtime.shutdown()
        restarted = build(host.db_path)
        try:
            async with restarted.client() as client:
                for request in (queued, intent):
                    url = f"/api/credentials/requests/{request['request_id']}"
                    state = (await client.get(url)).json()
                    assert (
                        state["state"] == "rejected" and state["delivery"]["status"] == "uncertain"
                    )
                    assert (await client.post(url + "/retry-delivery")).json() == state
            assert not restarted.backends
            assert not await restarted.system.held_prompts("c")
        finally:
            await restarted.runtime.shutdown()

    asyncio.run(exercise())


def test_origin_rejects_foreign_missing_and_retired_conversation(host: _Harness) -> None:
    async def exercise() -> None:
        await start(host)
        async with host.client() as client:
            for headers in (
                {},
                {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_failed"},
                {**HEADERS, "X-Plan-Conversation-ID": "other"},
            ):
                denied = await client.post(
                    "/api/credentials/requests", json=request_body(), headers=headers
                )
                assert (
                    denied.status_code == 400
                    and denied.json()["error"]["code"] == "agent_forbidden"
                ), denied.text
            pending = request_body()
            await client.post("/api/credentials/requests", json=pending, headers=HEADERS)
            conn = connect(str(host.db_path))
            conn.execute("UPDATE tickets SET conversation_id=NULL WHERE id='t_failed'")
            conn.commit()
            conn.close()
            denied = await client.post(
                "/api/credentials/requests", json=request_body(), headers=HEADERS
            )
            assert denied.json()["error"]["code"] == "agent_forbidden"
            rejected = await client.post(
                f"/api/credentials/requests/{pending['request_id']}/reject"
            )
            assert rejected.json()["delivery"]["status"] == "refused"
            assert (
                rejected.json()["delivery"]["refusal_reason"] == "requesting_conversation_retired"
            )
            assert not host.backends
        record = await host.store.read_conversation("c")
        assert record is not None
        identity = dict(record.resolved_start().role_materials.identity_environment_variables)
        assert identity["PLAN_CONVERSATION_ID"] == "c"

    asyncio.run(exercise())


def test_canceled_http_owner_write_still_delivers_before_a_racing_reset(
    host: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.credentials.requests.service import finish_operations
    from planner.runtime.conversation_start import reset_ticket_conversation

    async def exercise() -> None:
        await start(host)
        entered, release, reset_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = host.system.send_with_receipt

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(host.system, "send_with_receipt", blocked)
        async with host.client() as client:
            request = request_body()
            await client.post("/api/credentials/requests", json=request, headers=HEADERS)
            resolving = asyncio.create_task(
                client.post(f"/api/credentials/requests/{request['request_id']}/reject")
            )
            await entered.wait()
            conn = connect(str(host.db_path))

            async def reset() -> None:
                reset_entered.set()
                await reset_ticket_conversation(host.system, conn, "t_failed", now=10)

            resetting = asyncio.create_task(reset())
            await reset_entered.wait()
            await asyncio.sleep(0)
            assert not resetting.done()
            resolving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await resolving
            release.set()
            await finish_operations(host.runtime)
            await resetting
            state = data.read(conn, str(request["request_id"]))
            assert state["state"] == "rejected"
            assert state["delivery"]["status"] == "started"
            assert len(host.backend("c").written_texts) == 1
            assert (
                conn.execute("SELECT conversation_id FROM tickets WHERE id='t_failed'").fetchone()[
                    0
                ]
                is None
            )
            conn.close()

    asyncio.run(asyncio.wait_for(exercise(), 15))


def test_header_reuse_matches_required_header_before_bypassing_owner_input(host: _Harness) -> None:
    from planner.credentials.contracts import HeaderSecret

    async def exercise() -> None:
        await start(host)
        conn = connect(str(host.db_path))
        credentials.create_credential(
            conn,
            CredentialWrite(
                CredentialReference(CredentialScheme.local, "Header_key"),
                CredentialAuthKind.header,
                HeaderSecret("X-Api-Key", SECRET),
            ),
            principal=OWNER_PRINCIPAL,
            now=1,
            key_path=Path(host.app.state.config.credential_key_path),
        )
        async with host.client() as client:
            mismatched = await client.post(
                "/api/credentials/requests",
                json=request_body("Header_key", auth_kind="header", header_name="Authorization"),
                headers=HEADERS,
            )
            assert mismatched.status_code == 400 and "explicit replacement" in mismatched.text
            assert SECRET not in mismatched.text
            correct = await client.post(
                "/api/credentials/requests",
                json=request_body("Header_key", auth_kind="header", header_name="X-Api-Key"),
                headers=HEADERS,
            )
            assert correct.json()["state"] == "reused"
            assert SECRET not in correct.text
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 1
        conn.close()

    asyncio.run(exercise())


def test_request_transaction_and_receipt_companions_use_production_signal_admission(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        db = tmp_path / "signal-requests.db"
        signal = SignalRuntimeComposition(str(db), 5000)
        signal.bootstrap()
        from planner.navigation.delivery import DeliveryRuntime
        from planner.navigation.signal_runtime import SignalNavigationBinding

        binding = SignalNavigationBinding(signal.coordinator, DeliveryRuntime(db))
        host = build(db, signal=signal)
        signal.initialize()
        binding.attach()
        signal.coordinator.admit_runtime()
        conn = signal.connect()
        seed_test_model_catalogue(conn)
        conn.close()
        try:
            await start(host, busy=True)
            async with host.client() as client:
                body = request_body()
                created = await client.post("/api/credentials/requests", json=body, headers=HEADERS)
                assert created.status_code == 200, created.text
                resolved = await client.post(
                    f"/api/credentials/requests/{body['request_id']}/save",
                    json={"name": body["name"], "value": SECRET, "expected_revision": None},
                )
                assert resolved.status_code == 200, resolved.text
                assert resolved.json()["delivery"]["status"] == "injected"
                events = await host.store.read_events_after("c", 0)
                assert (
                    events[-1].sequence == (await host.store.read_conversation("c")).latest_sequence
                )
                snapshots = [
                    event
                    for event in events
                    if isinstance(event.payload, CredentialRequestEventPayload)
                ]
                assert snapshots[-1].payload.request == resolved.json()
                assert SECRET not in json.dumps(resolved.json())
        finally:
            await host.runtime.shutdown()
            binding.close()
            signal.close()

    asyncio.run(asyncio.wait_for(exercise(), 20))


@pytest.mark.parametrize("control", ["COMMIT", "ROLLBACK", "SAVEPOINT illicit"])
def test_domain_callback_cannot_escape_conversation_owned_transaction(
    host: _Harness, control: str
) -> None:
    import sqlite3

    async def exercise() -> None:
        await start(host)
        request = request_body()
        context = RequestContext(Principal(PrincipalKind.ticket, "t_failed"), "c")

        def forbidden(conn):
            actions.create(
                conn,
                request,
                ctx=context,
                now=1,
                key_path=Path(host.app.state.config.credential_key_path),
            )
            conn.execute(control)
            return None, ()

        with pytest.raises((sqlite3.DatabaseError, RuntimeError)):
            await host.system.record_events_transaction("c", forbidden)
        conn = connect(str(host.db_path))
        assert conn.execute("SELECT count(*) FROM credential_requests").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM credential_request_delivery_attempts").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT latest_sequence FROM conversations WHERE conversation_id='c'"
            ).fetchone()[0]
            == 0
        )
        conn.close()

    asyncio.run(exercise())


def test_owner_resolution_after_manager_deletion_is_refused_without_resurrecting_agent(
    host: _Harness,
) -> None:
    from planner.managers import data as managers
    from planner.managers import service as manager_service
    from planner.runtime.logic.conversation_start_resolution import manager_role_materials

    async def exercise() -> None:
        conn = connect(str(host.db_path))
        manager = managers.create_manager(
            conn,
            title="Temporary request manager",
            project_id="project_personal",
            clock=host.app.state.clock,
        )
        await host.system.start_conversation(
            ConversationStartRequest(
                conversation_id="manager-origin",
                model="gpt-6-sol",
                role_materials=manager_role_materials(manager.id),
                workspace_folder=host.db_path.parent,
            )
        )
        conn.execute(
            "UPDATE agents SET conversation_id='manager-origin' WHERE agent_key=?",
            (manager.manager_agent_key,),
        )
        conn.commit()
        async with host.client() as client:
            request = request_body()
            created = await client.post(
                "/api/credentials/requests",
                json=request,
                headers={
                    "X-Plan-Actor": "manager",
                    "X-Plan-Manager-ID": manager.id,
                    "X-Plan-Conversation-ID": "manager-origin",
                },
            )
            assert created.status_code == 200, created.text
            await manager_service.delete_manager(
                host.system, conn, manager.id, principal=OWNER_PRINCIPAL
            )
            resolved = await client.post(
                f"/api/credentials/requests/{request['request_id']}/reject"
            )
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["state"] == "rejected"
            assert resolved.json()["delivery"]["status"] == "refused"
            assert (
                resolved.json()["delivery"]["refusal_reason"] == "requesting_conversation_retired"
            )
            assert not host.backends
            assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
        conn.close()

    asyncio.run(exercise())
