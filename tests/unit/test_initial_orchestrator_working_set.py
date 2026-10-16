from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine

migration = importlib.import_module(
    "planner.core.migrations.versions.initial_orchestrator_working_set"
)


def test_initial_set_preserves_children_and_is_repeatable(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE sprint_items(id TEXT,title TEXT,settled INTEGER)")
        conn.exec_driver_sql("CREATE TABLE tickets(id TEXT,stage TEXT,sprint_item_id TEXT)")
        for item_id, title in migration.ACTIVE_ITEMS.items():
            conn.exec_driver_sql("INSERT INTO sprint_items VALUES(?,?,1)", (item_id, title))
        conn.exec_driver_sql("INSERT INTO sprint_items VALUES('older','Other outcome',0)")
        conn.exec_driver_sql("INSERT INTO tickets VALUES('child','needs_plan','older')")
        before = conn.exec_driver_sql("SELECT * FROM tickets").all()
        monkeypatch.setattr(migration.op, "get_bind", lambda: conn)
        migration.upgrade()
        expected = [("older", 1), ("si_8nbv749p", 0), ("si_dmhx1u7h", 0)]
        assert [
            tuple(row)
            for row in conn.exec_driver_sql("SELECT id,settled FROM sprint_items ORDER BY id")
        ] == expected
        migration.upgrade()
        assert [
            tuple(row)
            for row in conn.exec_driver_sql("SELECT id,settled FROM sprint_items ORDER BY id")
        ] == expected
        assert conn.exec_driver_sql("SELECT * FROM tickets").all() == before


@pytest.mark.parametrize("drift", ["missing", "renamed", "duplicate"])
def test_initial_set_refuses_identity_drift_before_updates(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE sprint_items(id TEXT,title TEXT,settled INTEGER)")
        for item_id, title in migration.ACTIVE_ITEMS.items():
            conn.exec_driver_sql("INSERT INTO sprint_items VALUES(?,?,0)", (item_id, title))
        if drift == "missing":
            conn.exec_driver_sql("DELETE FROM sprint_items WHERE id='si_8nbv749p'")
        elif drift == "renamed":
            conn.exec_driver_sql("UPDATE sprint_items SET title='New' WHERE id='si_8nbv749p'")
        else:
            conn.exec_driver_sql(
                "INSERT INTO sprint_items VALUES('duplicate',?,0)",
                (migration.ACTIVE_ITEMS["si_8nbv749p"],),
            )
        before = conn.exec_driver_sql("SELECT * FROM sprint_items").all()
        monkeypatch.setattr(migration.op, "get_bind", lambda: conn)
        with pytest.raises(RuntimeError, match="review exact active set"):
            migration.upgrade()
        assert conn.exec_driver_sql("SELECT * FROM sprint_items").all() == before


def test_initial_set_does_not_change_fresh_install_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE sprint_items(id TEXT,title TEXT,settled INTEGER DEFAULT 0)"
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: conn)
        migration.upgrade()
        conn.exec_driver_sql("INSERT INTO sprint_items(id,title) VALUES('new','New outcome')")
        assert conn.exec_driver_sql("SELECT settled FROM sprint_items").scalar_one() == 0
