"""Accepted staging source rewrites and navigation share one migration transaction.

All databases and runtime files are synthetic and remain under tmp_path.
"""

import json
import sqlite3
from typing import Any


def historical_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Inspect frozen schema 2 without admitting it through current public reads."""
    meta = conn.execute(
        "SELECT scope,schema_version,epoch,revision FROM navigation_meta"
    ).fetchone()
    return {
        "scope": meta[0],
        "schema_version": meta[1],
        "epoch": meta[2],
        "revision": str(meta[3]),
        "records": [
            {"kind": kind, "id": identity, "value": json.loads(value)}
            for kind, identity, value in conn.execute(
                "SELECT kind,id,value FROM navigation_records ORDER BY kind,id"
            )
        ],
    }
