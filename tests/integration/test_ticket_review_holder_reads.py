"""A supported holder transfer is visible through independent CLI reads."""

from __future__ import annotations

import json
from collections.abc import Callable

from tests.e2e.harness import JsonObject, ServerHandle


def test_ticket_reads_reflect_supported_holder_transfer(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
) -> None:
    created = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Read review holder",
        "--kickoff-note",
        "Expose the review holder.",
    )
    ticket_id = created["id"]
    commands = (
        ("ticket", "show", ticket_id),
        ("ticket", "show"),
        ("worker", "my-ticket"),
    )
    before = cli(server, *commands[0])
    assert before["header"]["ceiling_holder"] == {"kind": "owner", "id": "owner"}

    holder = {"kind": "chief", "id": "chief"}
    edited = cli(
        server,
        "ticket",
        "edit",
        ticket_id,
        "--input-json",
        "-",
        stdin=json.dumps({"ceiling_holder": holder}),
    )
    assert edited["ceiling_holder"] == holder
    for command in commands:
        for selected in ((), ("brief,guidance",)):
            record = cli(server, *command, *selected, ticket_id=ticket_id)
            assert record["header"]["ceiling_holder"] == holder
            assert record["header"]["stage"] == before["header"]["stage"]
            assert record["header"]["ceiling"] == before["header"]["ceiling"]
