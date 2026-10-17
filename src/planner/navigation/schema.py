"""Navigation-owned storage. Source rows remain authoritative."""

SCHEMA = (
    "CREATE TABLE navigation_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "scope TEXT NOT NULL, schema_version INTEGER NOT NULL, epoch TEXT NOT NULL, "
    "revision INTEGER NOT NULL CHECK(revision>=0))",
    "CREATE TABLE navigation_records (kind TEXT NOT NULL,id TEXT NOT NULL,"
    "value TEXT NOT NULL,PRIMARY KEY(kind,id))",
    "CREATE INDEX navigation_address_parent ON navigation_records("
    "json_extract(value,'$.parent_kind'),json_extract(value,'$.parent_id'),"
    "json_extract(value,'$.conversation_id')) WHERE kind='address'",
    "CREATE INDEX navigation_address_owner ON navigation_records("
    "json_extract(value,'$.owner_kind'),json_extract(value,'$.owner_id'),"
    "json_extract(value,'$.conversation_id')) WHERE kind='address'",
    "CREATE INDEX navigation_address_conversation ON navigation_records("
    "json_extract(value,'$.conversation_id')) WHERE kind='address'",
    "CREATE TABLE navigation_dependencies (source_kind TEXT NOT NULL,source_id TEXT NOT NULL,"
    "kind TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(source_kind,source_id,kind,id))",
    "CREATE INDEX navigation_dependencies_record ON navigation_dependencies(kind,id)",
    "CREATE TABLE navigation_root_records (root_kind TEXT NOT NULL,root_id TEXT NOT NULL,"
    "kind TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(root_kind,root_id,kind,id))",
    "CREATE TABLE navigation_dirty (kind TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(kind,id))",
    "CREATE TABLE navigation_journal (revision INTEGER PRIMARY KEY,body TEXT NOT NULL)",
)
