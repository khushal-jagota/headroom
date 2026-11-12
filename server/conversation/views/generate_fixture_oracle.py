"""Generate the view oracle from existing synthetic fixtures and unchanged Python."""

import hashlib
import json
import sys
from contextlib import closing
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    from tests.support.conversation_view_database import FIXTURES, database

    from planner.conversation.views.contracts import ConversationViewLens
    from planner.conversation.views.storage import encode_wire, read_page

    order = {
        "state": "conversation_id",
        "rows": "sequence",
        "turns": "source_sequence",
        "groups": "source_sequence",
        "lookups": "lookup_kind,lookup_id",
    }
    lenses: tuple[ConversationViewLens, ...] = ("focus", "full")
    result = []
    for fixture in FIXTURES:
        with closing(database(fixture["events"])) as connection:
            tables = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM conversation_view_{table} ORDER BY {columns}"
                    )
                ]
                for table, columns in order.items()
            }
            result.append(
                {
                    "name": fixture["name"],
                    "projection": tables,
                    "pages": {
                        lens: read_page(connection, "fixture", lens, stopped=fixture["stopped"])
                        for lens in lenses
                    },
                }
            )
    output = {
        "source_sha256": hashlib.sha256(
            (root / "tests/fixtures/conversation_views.json").read_bytes()
        ).hexdigest(),
        "fixtures": result,
    }
    destination = root / "server/conversation/views/fixture_oracle.json"
    destination.write_text(
        json.dumps(json.loads(encode_wire(output)), ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Generated {len(result)} synthetic fixture projections and both lens pages")


if __name__ == "__main__":
    main()
