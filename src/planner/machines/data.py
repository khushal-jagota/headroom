"""The two original destinations and their editable display identity."""

import sqlite3
from typing import cast, get_args

from planner.core.errors import ErrorCode, PlannerError
from planner.execution_identity import TicketLocale
from planner.machines.contracts import (
    HOME_MACHINE_ID,
    ORIGINAL_MAC_MACHINE_ID,
    Machine,
    MachineColor,
    MachineIcon,
)


def read_machine(conn: sqlite3.Connection, machine_id: str) -> Machine:
    row = conn.execute("SELECT * FROM machines WHERE machine_id=?", (machine_id,)).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.validation, "Unknown machine destination", {"machine_id": machine_id}
        )
    return Machine(
        str(row["machine_id"]),
        str(row["name"]),
        cast(MachineIcon, row["icon"]),
        cast(MachineColor, row["color"]),
        bool(row["is_home"]),
    )


def list_machines(conn: sqlite3.Connection) -> list[Machine]:
    return [
        read_machine(conn, str(row[0]))
        for row in conn.execute(
            "SELECT machine_id FROM machines ORDER BY is_home DESC, name, machine_id"
        )
    ]


def legacy_machine_id(locale: TicketLocale | str) -> str:
    return ORIGINAL_MAC_MACHINE_ID if locale == TicketLocale.macos else HOME_MACHINE_ID


def resolve_destination(
    conn: sqlite3.Connection, machine_id: str | None, locale: TicketLocale | str | None
) -> tuple[str, TicketLocale]:
    from planner.tickets.logic.locale import require_ticket_locale

    legacy = require_ticket_locale(locale) if locale is not None else None
    selected = (
        machine_id if machine_id is not None else legacy_machine_id(legacy or TicketLocale.vps)
    )
    read_machine(conn, selected)
    aliases = {HOME_MACHINE_ID: TicketLocale.vps, ORIGINAL_MAC_MACHINE_ID: TicketLocale.macos}
    if selected not in aliases:
        raise PlannerError(
            ErrorCode.validation, "This machine has no execution route", {"machine_id": selected}
        )
    if legacy is not None and selected != legacy_machine_id(legacy):
        raise PlannerError(
            ErrorCode.validation, "machine_id and locale name different destinations"
        )
    return selected, aliases[selected]


def edit_machine(conn: sqlite3.Connection, machine_id: str, body: dict[str, object]) -> Machine:
    old = read_machine(conn, machine_id)
    if set(body) - {"name", "icon", "color"}:
        raise PlannerError(ErrorCode.validation, "Only machine name, icon, and color are editable")
    name = body.get("name", old.name)
    icon = body.get("icon", old.icon)
    color = body.get("color", old.color)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise PlannerError(ErrorCode.validation, "Machine name must contain 1 to 80 characters")
    if icon not in get_args(MachineIcon) or color not in get_args(MachineColor):
        raise PlannerError(ErrorCode.validation, "Unknown machine icon or color")
    conn.execute(
        "UPDATE machines SET name=?,icon=?,color=? WHERE machine_id=?",
        (name.strip(), icon, color, machine_id),
    )
    conn.commit()
    return read_machine(conn, machine_id)
