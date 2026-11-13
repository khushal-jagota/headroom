"""Generate trusted TypeScript DDL from a private, fully migrated Python database.

This explicit build helper ships no database image, identity, or domain row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from planner.core.db import connect, create_schema
from planner.environments.update_install import migration_graph

ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = ROOT / "server/core/database/application_schema.json"
GRAPH_ARTIFACT = ROOT / "server/environments/release/migration_graph.json"


def build_migration_graph() -> dict[str, Any]:
    directory = ROOT / "src/planner/core/migrations/versions"
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "version": 1,
        "source_digest": digest.hexdigest(),
        "revisions": {
            revision: list(parents) for revision, parents in migration_graph(ROOT).items()
        },
    }


def build_seed(root: Path) -> tuple[dict[str, Any], dict[str, int]]:
    root.mkdir(parents=True, exist_ok=True)
    database = root / "source.sqlite"
    with closing(connect(str(database))) as connection:
        create_schema(connection)
        statements = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY "
                "CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name"
            )
        ]
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(revisions) != 1:
            raise RuntimeError("the application seed requires one exact Alembic frontier")
        inventory = {}
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            quoted = '"' + name.replace('"', '""') + '"'
            count = int(connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0])
            if count:
                inventory[name] = count
    return {"statements": statements, "alembicRevision": revisions[0][0]}, inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ARTIFACT)
    parser.add_argument("--graph-output", type=Path, default=GRAPH_ARTIFACT)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="unit21-schema-") as temporary:
        seed, inventory = build_seed(Path(temporary))
    graph = build_migration_graph()
    heads = set(graph["revisions"]) - {
        parent for parents in graph["revisions"].values() for parent in parents
    }
    if heads != {seed["alembicRevision"]}:
        raise SystemExit("migration graph differs from the generated schema frontier")
    content = json.dumps(seed, indent=2, ensure_ascii=False) + "\n"
    graph_content = json.dumps(graph, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if args.output.read_text() != content:
            raise SystemExit(
                "application schema seed differs from the current Python migration frontier"
            )
        if args.graph_output.read_text() != graph_content:
            raise SystemExit("migration graph differs from the current Python migration source")
    else:
        args.output.write_text(content)
        args.graph_output.write_text(graph_content)
    print(
        json.dumps(
            {
                "alembicRevision": seed["alembicRevision"],
                "statements": len(seed["statements"]),
                "fresh_state_inventory": inventory,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
