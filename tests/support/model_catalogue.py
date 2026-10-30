"""Explicit local catalogue evidence for tests of unrelated Ticket behavior.

These are scripted provider answers, not production model defaults. Tests of catalogue
discovery and cold failures create their own schema without this fixture.
"""

import json
import sqlite3
import time

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.logic.variants import recognize_variant


def seed_test_model_catalogue(conn: sqlite3.Connection) -> None:
    models = [
        (profile["default_backend"], profile["default_model"], profile["default_reasoning_effort"])
        for row in conn.execute("SELECT definition_json FROM worker_types")
        for profile in [json.loads(row[0])["profile"]]
    ]
    models.extend(("claude", alias, None) for alias in ("opus", "sonnet", "haiku"))
    for backend, model, default_effort in models:
        variant = recognize_variant(ConversationBackendKey(backend), model)
        if variant is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO model_variant_resolutions "
            "(backend_key,variant,model,supported_reasoning_efforts,observed_at) "
            "VALUES (?,?,?,?,?)",
            (
                backend,
                variant.name,
                model,
                json.dumps(
                    list(
                        dict.fromkeys(
                            ["low", "medium", "high", "xhigh", "max", "ultra"]
                            + ([default_effort] if default_effort is not None else [])
                        )
                    )
                ),
                int(time.time()),
            ),
        )
