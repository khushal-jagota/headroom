"""Protect the oracle against false equality, which self-calibration cannot expose."""

from pathlib import Path

import pytest
from tests.compare.runner import Normalizer


def test_payload_differences_survive_normalization(tmp_path: Path) -> None:
    left, right = Normalizer({}, tmp_path), Normalizer({}, tmp_path)
    assert left.normalize({"name": "panels_original"}) != right.normalize(
        {"name": "panels_modified"}
    )
    assert left.normalize({"count": 1}) != right.normalize({"count": 2})
    assert left.normalize(["a", "b"]) != right.normalize(["b", "a"])
    assert left.normalize(["t_12345678", "t_12345678"]) != right.normalize(
        ["t_abcdefgh", "t_abcdefgh", "t_abcdefgh"]
    )
    # Equivalent aliases map identically, but a changed reference must still differ.
    assert left.normalize("t_12345678") == right.normalize("t_abcdefgh")
    assert left.normalize("t_12345678") != right.normalize("t_87654321")
    left, right = Normalizer({}, tmp_path), Normalizer({}, tmp_path)
    assert left.normalize("ticket_decision_" + "a" * 64) == right.normalize(
        "ticket_decision_" + "b" * 64
    )
    assert left.normalize("a" * 64) != right.normalize("b" * 64)
    # Only declared float fields admit native JSON number spelling.
    numbers = Normalizer({"response_floats": {"body.value": "declared float"}}, tmp_path)
    assert numbers.normalize({"body": {"value": 9.007199254740993e20}}) == numbers.normalize(
        {"body": {"value": 900719925474099300000}}
    )
    assert numbers.normalize({"body": {"value": 1}}) != numbers.normalize({"body": {"value": 2}})
    assert numbers.normalize({"revision": 9007199254740992}) != numbers.normalize(
        {"revision": 9007199254740993}
    )
    with pytest.raises(AssertionError):
        numbers.normalize({"body": {"value": True}})


def test_opaque_tokens_preserve_change_history(tmp_path: Path) -> None:
    rules = {"response_tokens": {"revision": "representation-dependent counter"}}
    left, right = Normalizer(rules, tmp_path), Normalizer(rules, tmp_path)
    assert left.normalize({"revision": 1}, "resource") == right.normalize(
        {"revision": 100}, "resource"
    )
    assert left.normalize({"revision": 2}, "resource") == right.normalize(
        {"revision": 102}, "resource"
    )
    assert left.normalize({"revision": 2}, "resource") != right.normalize(
        {"revision": 103}, "resource"
    )


def test_source_rows_and_user_json_remain_significant(tmp_path: Path) -> None:
    import sqlite3

    from tests.compare.runner import rows

    rules = {"response_ignore": {"body.version.scope": "projection identity"}}
    snapshots = []
    for name, value in [("left", 1), ("right", 2)]:
        path = tmp_path / f"{name}.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE source (id TEXT PRIMARY KEY, value INTEGER, payload TEXT)")
            conn.execute(
                "INSERT INTO source VALUES (?, ?, ?)", ("fixed", value, '{"scope":"user"}')
            )
        snapshots.append(rows(path, Normalizer(rules, tmp_path)))
    assert snapshots[0] != snapshots[1]
    normalizer = Normalizer(rules, tmp_path)
    assert normalizer.normalize({"body": {"payload": {"scope": "a"}}}) != normalizer.normalize(
        {"body": {"payload": {"scope": "b"}}}
    )
    assert normalizer.normalize('{"a":1}') != normalizer.normalize('{ "a": 1 }')


def test_scoped_rule_does_not_cross_payload_boundaries(tmp_path: Path) -> None:
    rules = {"response_tokens": {"body.tickets.*.record_sha256": "opaque ticket digest"}}
    normalizer = Normalizer(rules, tmp_path)
    first = {"body": {"tickets": [{"field_values": {"record_sha256": "first"}}]}}
    second = {"body": {"tickets": [{"field_values": {"record_sha256": "second"}}]}}
    assert normalizer.normalize(first) != normalizer.normalize(second)
    route = "GET /api/navigation/view/snapshot?view=invalid"
    diagnostics = Normalizer({"response_text": {route + ".body.detail": "native text"}}, tmp_path)
    source, native = {"body": {"detail": "Python text"}}, {"body": {"detail": "Node text"}}
    assert diagnostics.normalize(source, route) == diagnostics.normalize(native, route)
    assert diagnostics.normalize(source, "other") != diagnostics.normalize(native, "other")
    assert diagnostics.normalize(source, route) != diagnostics.normalize({"body": {}}, route)
    with pytest.raises(AssertionError):
        diagnostics.normalize({"body": {"detail": 1}}, route)


def test_known_parent_rows_ignore_physical_insertion_order(tmp_path: Path) -> None:
    import sqlite3

    from tests.compare.runner import rows

    conversation_ids = ["conv_" + "a" * 32, "conv_" + "b" * 32]
    snapshots = []
    for index, order in enumerate(((0, 1), (1, 0))):
        path = tmp_path / f"{index}.db"
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE conversation_turn_occurrences ("
                "conversation_id TEXT, turn_number INTEGER, occurrence_id TEXT, "
                "runtime_epoch TEXT, PRIMARY KEY(conversation_id, turn_number))"
            )
            for item in order:
                conn.execute(
                    "INSERT INTO conversation_turn_occurrences VALUES (?, 2, ?, ?)",
                    (conversation_ids[item], f"{index * 2 + item + 1:032x}", "e" * 32),
                )
        normalizer = Normalizer({}, tmp_path)
        normalizer.normalize(conversation_ids)
        snapshots.append(rows(path, normalizer))
    assert snapshots[0] == snapshots[1]
