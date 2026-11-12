"""Generate the view oracle from existing synthetic fixtures and unchanged Python."""

import argparse
import hashlib
import json
import sys
from contextlib import closing
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed oracle differs")
    arguments = parser.parse_args()
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
    generated = (
        json.dumps(json.loads(encode_wire(output)), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    if arguments.check:
        if destination.read_bytes() != generated:
            parser.exit(1, "Conversation view oracle differs from current Python. Regenerate it.\n")
        print(f"Verified {len(result)} synthetic fixture projections and both lens pages")
    else:
        destination.write_bytes(generated)
        print(f"Generated {len(result)} synthetic fixture projections and both lens pages")


if __name__ == "__main__":
    main()
