"""Generate trusted TypeScript DDL from a private, fully migrated Python database.

This explicit build helper ships no database image, identity, or domain row.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = ROOT / "server/core/database/application_schema.json"


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
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="unit21-schema-") as temporary:
        seed, inventory = build_seed(Path(temporary))
    content = json.dumps(seed, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if args.output.read_text() != content:
            raise SystemExit(
                "application schema seed differs from the current Python migration frontier"
            )
    else:
        args.output.write_text(content)
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
