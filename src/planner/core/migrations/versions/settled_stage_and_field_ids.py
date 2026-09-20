"""Move the stage and field ids to the settled names.

Revision ID: settled_stage_and_field_ids
Revises: one_ticket_ending

The labels were settled first and a separate change moved them. An id is not a label: two
of these are behaviour. `closeout` is the field every Worker type must declare, the one
the Closeout lane forms around, and the one `waiting_to_closeout` is derived from.
`kickoff` is paired first by the same guard. So the ids move here, with the code that
reads them, and before the migration chain collapses to a new baseline — a rename that
arrives after the baseline lives in the chain forever.

An id moves only because its label moved. Nineteen field ids keep their labels and so keep
their ids; nothing moves for tidiness.

The mapping is per Worker type, and that is not a precaution. `direction` has two
destinations: product_design's becomes `best_guess_and_open_options` and planning-day's
becomes `todays_direction`. `understanding` moves in new_worker and stays in exploration,
whose rename was withdrawn. `research` moves while `research_plan` does not. A global
replace gets each of those wrong, so there is no correct global replace to write.

A missed key does not raise. `values_from_json` rebuilds a Ticket's text from the declared
field ids, so a key this migration fails to move is dropped silently on the next read and
the text goes dark. Counting is therefore the correctness argument rather than a check for
tidiness, and it runs inside this transaction so that a mismatch aborts the whole upgrade.

History is not rewritten. Conversation events hold about a hundred thousand mentions of the
old ids, and the bodies inside `field_values` are prose a person or a worker wrote. Those
say what was said. Only keys, stage positions and declarations move.
"""

from __future__ import annotations

import json
import re
from typing import Any

from alembic import op

revision = "settled_stage_and_field_ids"
down_revision = "one_ticket_ending"
branch_labels = None
depends_on = None


FIELD_ID_RENAMES: dict[str, dict[str, str]] = {
    "coding": {
        "kickoff": "brief",
        "success": "success_condition",
        "approach": "what_changes",
        "closeout": "consequences",
    },
    "general": {
        "kickoff": "brief",
        "execution": "work_done",
        "closeout": "consequences",
    },
    "debugging": {
        "kickoff": "brief",
        "structural_diagnosis": "root_cause",
        "solution": "proposed_fix",
        "closeout": "consequences",
    },
    "new_worker": {
        "kickoff": "brief",
        "understanding": "purpose_and_boundaries",
        "thinking": "what_good_looks_like_at_each_stage",
        "runtime_defaults": "model_and_effort",
        "closeout": "consequences",
    },
    "amend_worker": {
        "kickoff": "brief",
        "closeout": "consequences",
    },
    "exploration": {
        "kickoff": "brief",
        "research": "findings",
        "follow_up": "proposed_follow_up",
        "closeout": "consequences",
    },
    "initiative_planning": {
        "kickoff": "brief",
        "rough_shape": "rough_split_into_parts",
        "ticket_outlines": "proposed_tickets",
        "closeout": "consequences",
    },
    "initiative_review": {
        "kickoff": "brief",
        "closeout": "consequences",
    },
    "product_design": {
        "kickoff": "brief",
        "direction": "best_guess_and_open_options",
        "closeout": "consequences",
    },
    "planning-day": {
        "direction": "todays_direction",
        "closeout": "consequences",
    },
    "planning-midday-check": {
        "kickoff": "brief",
        "action": "agreed_intervention",
        "closeout": "consequences",
    },
    "planning-sprint": {
        "kickoff": "brief",
        "closeout": "consequences",
    },
    "personal": {
        "kickoff": "brief",
        "closeout": "consequences",
    },
    "research": {
        "kickoff": "brief",
        "research": "findings",
        "closeout": "consequences",
    },
}


def _renamed_stage_id(stage_id: str, field_renames: dict[str, str]) -> str:
    """A Stage id moves only when it is literally ``needs_`` plus the field it gates.

    Every shipped type spells its Stages that way, but the rule does not require it and the
    probe Worker type deliberately does not. Such a Stage keeps its own name and only the
    field it points at moves.
    """
    for old_field_id, new_field_id in field_renames.items():
        if stage_id == f"needs_{old_field_id}":
            return f"needs_{new_field_id}"
    return stage_id


def renamed_definition(decoded: dict[str, Any]) -> dict[str, Any]:
    """Apply this change to one decoded Worker type record.

    Exported because the seeding migration carries a frozen copy of what shipped, and the
    test suite reads that copy. The frozen copy is not edited, so the tests compose this
    the same way they already compose the ``dropped`` removal.
    """
    field_renames = FIELD_ID_RENAMES.get(str(decoded.get("worker_type")), {})
    if not field_renames:
        return decoded
    moved: dict[str, Any] = dict(decoded)
    moved["stages"] = [
        {
            **stage,
            "id": _renamed_stage_id(str(stage["id"]), field_renames),
            "gating_field": (
                None
                if stage.get("gating_field") is None
                else field_renames.get(str(stage["gating_field"]), stage["gating_field"])
            ),
        }
        for stage in decoded["stages"]
    ]
    moved["fields"] = [
        {**field, "id": field_renames.get(str(field["id"]), field["id"])}
        for field in decoded["fields"]
    ]
    return moved


def _declared(definition: dict[str, Any]) -> tuple[set[str], set[str]]:
    return (
        {str(stage["id"]) for stage in definition["stages"]},
        {str(field["id"]) for field in definition["fields"]},
    )


def _escaped(identifier: str) -> str:
    """How skill Markdown spells an id, which escapes the underscores it contains."""
    return identifier.replace("_", r"\_")


def moved_skill_text(text: str, definition: dict[str, Any]) -> str:
    r"""Move the ids one Worker type's own specialist skill names.

    Two different rules, because the two kinds of id carry different risk.

    A Stage id is unambiguous. Nothing but a Stage is called ``needs_something``, so it
    moves wherever it appears — in a heading, in bold, in backticks, or bare.

    A field id is an ordinary word. ``action``, ``thinking`` and ``success`` are all words
    a skill uses in a sentence, and a skill also writes the *label* in lowercase bold. So a
    field id moves only inside backticks, where it can only be a literal a worker types. A
    bold or bare word is left for the change that owns the labels.

    Both spellings are handled, because Markdown escapes the underscores in bold.
    """
    field_renames = FIELD_ID_RENAMES.get(str(definition.get("worker_type")), {})
    for old_field_id, new_field_id in field_renames.items():
        for old_stage_id, new_stage_id in (
            (f"needs_{old_field_id}", f"needs_{new_field_id}"),
            (_escaped(f"needs_{old_field_id}"), _escaped(f"needs_{new_field_id}")),
        ):
            # The trailing guard excludes an escaped underscore as well as a plain one,
            # so `needs\_success` does not match inside `needs\_success\_condition` and
            # running this twice changes nothing the second time.
            text = re.sub(
                rf"(?<![0-9A-Za-z_\\]){re.escape(old_stage_id)}(?![0-9A-Za-z_]|\\_)",
                new_stage_id.replace("\\", "\\\\"),
                text,
            )
        text = text.replace(f"`{old_field_id}`", f"`{new_field_id}`")
    return text


def upgrade() -> None:
    conn = op.get_bind()

    definitions: dict[str, dict[str, Any]] = {}
    for worker_type, definition_json in conn.exec_driver_sql(
        "SELECT worker_type, definition_json FROM worker_types"
    ).fetchall():
        moved = renamed_definition(json.loads(definition_json))
        definitions[str(worker_type)] = moved
        conn.exec_driver_sql(
            "UPDATE worker_types SET definition_json = ? WHERE worker_type = ?",
            (json.dumps(moved, ensure_ascii=False), worker_type),
        )

    keys_before = 0
    keys_after = 0
    for ticket_id, worker_type, stage, ceiling, field_values, pending_proposal in (
        conn.exec_driver_sql(
            "SELECT id, worker_type, stage, ceiling, field_values, pending_proposal FROM tickets"
        ).fetchall()
    ):
        field_renames = FIELD_ID_RENAMES.get(str(worker_type), {})
        stored = json.loads(field_values)
        keys_before += len(stored)
        if not field_renames:
            keys_after += len(stored)
            continue
        moved_values = {field_renames.get(key, key): value for key, value in stored.items()}
        keys_after += len(moved_values)
        moved_proposal = pending_proposal
        if pending_proposal is not None:
            decoded_proposal = json.loads(pending_proposal)
            decoded_proposal["field"] = field_renames.get(
                str(decoded_proposal["field"]), decoded_proposal["field"]
            )
            moved_proposal = json.dumps(decoded_proposal, ensure_ascii=False)
        conn.exec_driver_sql(
            "UPDATE tickets SET stage = ?, ceiling = ?, field_values = ?, pending_proposal = ? "
            "WHERE id = ?",
            (
                _renamed_stage_id(str(stage), field_renames),
                _renamed_stage_id(str(ceiling), field_renames),
                json.dumps(moved_values, ensure_ascii=False),
                moved_proposal,
                ticket_id,
            ),
        )

    # A rename must not merge two keys into one. Nothing else in this migration would
    # notice if it did, because the loser simply stops existing.
    if keys_before != keys_after:
        raise RuntimeError(
            f"renaming field keys changed the stored text: {keys_before} keys before, "
            f"{keys_after} after"
        )

    for table in ("ticket_paired_stage_openers", "ticket_revision_feedback"):
        for row_id, worker_type, stage in conn.exec_driver_sql(
            f"SELECT o.rowid, t.worker_type, o.stage FROM {table} o "
            "JOIN tickets t ON t.id = o.ticket_id"
        ).fetchall():
            field_renames = FIELD_ID_RENAMES.get(str(worker_type), {})
            moved_stage = _renamed_stage_id(str(stage), field_renames)
            if moved_stage != stage:
                conn.exec_driver_sql(
                    f"UPDATE {table} SET stage = ? WHERE rowid = ?", (moved_stage, row_id)
                )

    for definition in definitions.values():
        skill_name = definition["profile"]["specialist_skill"]
        row = conn.exec_driver_sql(
            "SELECT source_text FROM managed_skills WHERE skill_name = ?", (skill_name,)
        ).fetchone()
        if row is None:
            continue
        source_text = str(row[0])
        corrected = moved_skill_text(source_text, definition)
        if corrected != source_text:
            conn.exec_driver_sql(
                "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
                (corrected, skill_name),
            )

    _require_every_stored_id_is_declared(conn, definitions)


def _require_every_stored_id_is_declared(conn: Any, definitions: dict[str, Any]) -> None:
    """Nothing may be left standing on an id the Worker types no longer declare.

    This is the whole proof. It runs before the transaction closes, so raising here rolls
    the rename back rather than leaving Tickets that read as empty.
    """
    for ticket_id, worker_type, stage, ceiling, field_values, pending_proposal in (
        conn.exec_driver_sql(
            "SELECT id, worker_type, stage, ceiling, field_values, pending_proposal FROM tickets"
        ).fetchall()
    ):
        definition = definitions.get(str(worker_type))
        if definition is None:
            continue
        stage_ids, field_ids = _declared(definition)
        stranded = {
            "stage": {str(stage)} - stage_ids,
            "ceiling": {str(ceiling)} - stage_ids,
            "field_values": set(json.loads(field_values)) - field_ids,
            "pending_proposal": (
                set()
                if pending_proposal is None
                else {str(json.loads(pending_proposal)["field"])} - field_ids
            ),
        }
        undeclared = {place: left for place, left in stranded.items() if left}
        if undeclared:
            raise RuntimeError(
                f"ticket {ticket_id} ({worker_type}) holds ids its Worker type no longer "
                f"declares: {undeclared}"
            )

    for table in ("ticket_paired_stage_openers", "ticket_revision_feedback"):
        for ticket_id, worker_type, stage in conn.exec_driver_sql(
            f"SELECT o.ticket_id, t.worker_type, o.stage FROM {table} o "
            "JOIN tickets t ON t.id = o.ticket_id"
        ).fetchall():
            definition = definitions.get(str(worker_type))
            if definition is None:
                continue
            stage_ids, _field_ids = _declared(definition)
            if str(stage) not in stage_ids:
                raise RuntimeError(
                    f"{table} holds stage {stage!r} for ticket {ticket_id} ({worker_type}), "
                    "which its Worker type no longer declares"
                )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
