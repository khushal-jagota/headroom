"""Retained uncertain starts must not lengthen another start's writer transaction."""

from sqlite3 import Connection


def test_exact_turn_admission_lookup_does_not_scan_retained_bindings(tmp_db: Connection) -> None:
    tmp_db.executemany(
        "INSERT INTO automatic_start_bindings "
        "(identity,work_class,destination,conversation_id,turn_number) VALUES (?,?,?,?,?)",
        ((f"sender-{number}", "worker", "home", f"c-{number}", 1) for number in range(1700)),
    )
    steps = 0

    def advance() -> int:
        nonlocal steps
        steps += 1
        return 0

    tmp_db.set_progress_handler(advance, 1)
    try:
        found = tmp_db.execute(
            "SELECT 1 FROM automatic_start_bindings WHERE conversation_id=? AND turn_number=?",
            ("c-1699", 1),
        ).fetchone()
    finally:
        tmp_db.set_progress_handler(None, 0)
    assert found is not None
    # An exact composite-key lookup takes tens of instructions. A scan takes thousands.
    assert steps < 100
