"""The Mac page's read (health_payload) shows measured contact and the stored refusal.

Contact is presence memory, flushed to mac_executor_presence at most every interval; a
refusal is an ExecutorRefusal in the presence row. Requests the VPS refuses still prove
the Mac made contact, and the last contact outlives the VPS process that measured it.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from tests.support.executor_presence import lease_request, mac_identity

from planner.core.clock import RealClock
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.execution_identity import MAC_NEEDS_UPDATE
from planner.executor.protocol import LeaseRefused, as_object
from planner.executor.vps_transport import VpsExecutor


def _database(tmp_path: Path) -> str:
    path = str(tmp_path / "planning.db")
    connection = connect(path)
    try:
        create_schema(connection)
    finally:
        connection.close()
    return path


def test_refused_requests_advance_contact_and_the_read_survives_a_vps_restart(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)
    old = replace(mac_identity(), protocol_version=21)

    async def exercise() -> None:
        endpoint = VpsExecutor(path, RealClock())
        try:
            assert endpoint.health_payload()["last_contact_at_ms"] is None
            refused = await endpoint.lease(lease_request(old))
            assert isinstance(refused, LeaseRefused) and refused.refusal is not None
            payload = endpoint.health_payload()
            assert payload["presence"] == "refused"
            assert payload["refusal"] == as_object(refused.refusal)
            assert payload["last_contact_at_ms"] == refused.refusal.refused_at_ms
            await asyncio.sleep(0.01)
            # A work request fenced off is still contact, measured at once.
            try:
                endpoint.require_current(
                    incarnation_header=old.executor_incarnation, protocol_header="21"
                )
            except PlannerError:
                pass
            else:
                raise AssertionError("a refused process passed the fence")
            fenced_contact = endpoint.health_payload()["last_contact_at_ms"]
            assert isinstance(fenced_contact, int)
            assert fenced_contact > refused.refusal.refused_at_ms
        finally:
            # Shutdown flushes the contact the interval had not yet written.
            await endpoint.close()
        restarted = VpsExecutor(path, RealClock())
        try:
            payload = restarted.health_payload()
            assert payload["last_contact_at_ms"] == fenced_contact
            assert payload["presence"] == "refused"
            refusal = payload["refusal"]
            assert isinstance(refusal, dict) and refusal["reason"] == MAC_NEEDS_UPDATE
            assert refusal["peer_identity"] == as_object(old)
        finally:
            await restarted.close()

    asyncio.run(asyncio.wait_for(exercise(), 20))
