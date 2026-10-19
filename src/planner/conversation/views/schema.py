"""Rebuildable projection schema. The ordinary migration owns installation."""

SCHEMA_STATEMENTS = (
    """CREATE TABLE conversation_view_state (
        conversation_id TEXT PRIMARY KEY
        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        projection_version INTEGER NOT NULL, through_sequence INTEGER NOT NULL DEFAULT 0,
        current_turn_key TEXT, current_group_key TEXT, plan_sequence INTEGER,
        segment_start_sequence INTEGER NOT NULL DEFAULT 0,
        plan_summary TEXT, plan_body_bytes INTEGER NOT NULL DEFAULT 0,
        latest_full_happened_sequence INTEGER, latest_focus_happened_sequence INTEGER
    )""",
    """CREATE TABLE conversation_view_rows (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL, row_kind TEXT NOT NULL, created_at INTEGER NOT NULL,
        settlement_sequence INTEGER, changed_sequence INTEGER NOT NULL, sender_message_id TEXT,
        turn_key TEXT, work_group_key TEXT, focus_visible INTEGER NOT NULL,
        rest_summary TEXT, permission_title_excerpt TEXT,
        removed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (conversation_id, sequence)
    )""",
    "CREATE INDEX conversation_view_rows_kind ON "
    "conversation_view_rows(conversation_id, row_kind, sequence)",
    "CREATE INDEX conversation_view_rows_changed ON "
    "conversation_view_rows(conversation_id, changed_sequence, sequence)",
    "CREATE INDEX conversation_view_rows_focus ON "
    "conversation_view_rows(conversation_id, focus_visible, removed, sequence)",
    "CREATE INDEX conversation_view_rows_turn ON "
    "conversation_view_rows(conversation_id, turn_key, removed, sequence)",
    "CREATE INDEX conversation_view_rows_group ON "
    "conversation_view_rows(conversation_id, work_group_key, removed, sequence)",
    """CREATE TABLE conversation_view_turns (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        turn_key TEXT NOT NULL, source_sequence INTEGER NOT NULL, created_at INTEGER NOT NULL,
        started_at_milliseconds INTEGER NOT NULL, focus_head INTEGER NOT NULL,
        ending_sequence INTEGER, ending TEXT, ended_at INTEGER,
        full_message_count INTEGER NOT NULL DEFAULT 0,
        focus_message_count INTEGER NOT NULL DEFAULT 0,
        latest_full_message_sequence INTEGER, latest_focus_message_sequence INTEGER,
        tool_call_count INTEGER NOT NULL DEFAULT 0, changed_sequence INTEGER NOT NULL,
        PRIMARY KEY (conversation_id, turn_key)
    )""",
    "CREATE INDEX conversation_view_turns_order ON "
    "conversation_view_turns(conversation_id, source_sequence)",
    "CREATE INDEX conversation_view_turns_focus_order ON "
    "conversation_view_turns(conversation_id, focus_head, source_sequence)",
    "CREATE INDEX conversation_view_turns_changed ON "
    "conversation_view_turns(conversation_id, changed_sequence)",
    """CREATE TABLE conversation_view_groups (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        group_key TEXT NOT NULL, source_sequence INTEGER NOT NULL, turn_key TEXT,
        entry_count INTEGER NOT NULL, latest_entry_sequence INTEGER NOT NULL,
        ending_sequence INTEGER,
        changed_sequence INTEGER NOT NULL,
        PRIMARY KEY (conversation_id, group_key)
    )""",
    "CREATE INDEX conversation_view_groups_turn ON "
    "conversation_view_groups(conversation_id, turn_key, source_sequence)",
    "CREATE INDEX conversation_view_groups_order ON "
    "conversation_view_groups(conversation_id, source_sequence)",
    """CREATE TABLE conversation_view_lookups (
        conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        lookup_kind TEXT NOT NULL, lookup_id TEXT NOT NULL,
        source_sequence INTEGER NOT NULL, turn_key TEXT,
        PRIMARY KEY (conversation_id, lookup_kind, lookup_id)
    )""",
    "CREATE INDEX conversation_view_lookups_turn ON "
    "conversation_view_lookups(conversation_id, turn_key, lookup_kind)",
)
