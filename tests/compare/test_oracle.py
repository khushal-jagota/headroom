"""Protect the oracle against false equality, which self-calibration cannot expose."""

from pathlib import Path

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
