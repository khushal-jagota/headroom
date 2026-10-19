"""Physical preparation never grants Signal authority or publishes view rows."""

import sqlite3

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS navigation_view_meta (singleton INTEGER PRIMARY KEY "
    "CHECK(singleton=1),projection_version INTEGER NOT NULL,epoch TEXT NOT NULL,"
    "revision INTEGER NOT NULL,ordering_generation TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS navigation_view_index (kind TEXT NOT NULL,id TEXT NOT NULL,"
    "content_hash TEXT NOT NULL,conversation_id TEXT,parent_kind TEXT,parent_id TEXT,"
    "owner_kind TEXT,owner_id TEXT,project_id TEXT,working INTEGER NOT NULL,"
    "out_claim INTEGER NOT NULL,review INTEGER NOT NULL,unread INTEGER NOT NULL,"
    "settled INTEGER NOT NULL,PRIMARY KEY(kind,id))",
    "CREATE INDEX IF NOT EXISTS navigation_view_pointer ON navigation_view_index("
    "conversation_id,kind,id)",
    "CREATE INDEX IF NOT EXISTS navigation_view_parent ON navigation_view_index("
    "parent_kind,parent_id,kind,id)",
    "CREATE INDEX IF NOT EXISTS navigation_view_owner ON navigation_view_index("
    "owner_kind,owner_id,kind,id)",
    "CREATE TABLE IF NOT EXISTS navigation_view_memberships ("
    "section_key TEXT NOT NULL,profile TEXT NOT NULL,locale TEXT NOT NULL,"
    "kind TEXT NOT NULL,id TEXT NOT NULL,rank INTEGER NOT NULL,title_key BLOB NOT NULL,"
    "id_key BLOB NOT NULL,PRIMARY KEY(section_key,profile,locale,kind,id))",
    "CREATE INDEX IF NOT EXISTS navigation_view_window ON navigation_view_memberships("
    "section_key,profile,locale,rank,title_key,id_key,kind,id)",
    "CREATE INDEX IF NOT EXISTS navigation_view_member ON navigation_view_memberships(kind,id)",
    "CREATE TABLE IF NOT EXISTS navigation_view_counts (section_key TEXT NOT NULL,"
    "profile TEXT NOT NULL,locale TEXT NOT NULL,value INTEGER NOT NULL CHECK(value>=0),"
    "PRIMARY KEY(section_key,profile,locale))",
    "CREATE TABLE IF NOT EXISTS navigation_view_current_conversations ("
    "conversation_id TEXT PRIMARY KEY,reference_count INTEGER NOT NULL CHECK(reference_count>0),"
    "working INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS navigation_view_unread_conversations ("
    "conversation_id TEXT PRIMARY KEY,reference_count INTEGER NOT NULL CHECK(reference_count>0))",
    "CREATE TABLE IF NOT EXISTS navigation_view_globals (name TEXT PRIMARY KEY,"
    "value INTEGER NOT NULL CHECK(value>=0))",
    "CREATE TABLE IF NOT EXISTS navigation_view_changes (revision INTEGER PRIMARY KEY,"
    "footprint TEXT NOT NULL)",
)


def prepared(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='navigation_view_meta'").fetchone()
        is not None
    )


def prepare(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("view preparation requires a transaction")
    for statement in SCHEMA:
        conn.execute(statement)
