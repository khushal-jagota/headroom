"""Keep encrypted values intact while separating names from source metadata."""

import json

from alembic import op

revision = "reusable_named_credentials"
down_revision = "primary_goal_checklist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    objects = [
        row[0]
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name='credentials' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL"
        )
    ]
    rows = conn.exec_driver_sql("SELECT * FROM credentials").mappings().all()
    conn.exec_driver_sql("""CREATE TABLE credentials_reusable (
      id TEXT PRIMARY KEY, scheme TEXT NOT NULL CHECK(scheme='local'), name TEXT NOT NULL,
      auth_kind TEXT NOT NULL CHECK(auth_kind IN ('bearer','header','basic')),
      encryption_version INTEGER NOT NULL CHECK(encryption_version IN (1,2)),
      encryption_context BLOB NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
      revision INTEGER NOT NULL CHECK(revision>0),
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
    for row in rows:
        context = json.dumps(
            {key: row[key] for key in ("project_id", "account", "scheme", "name", "auth_kind")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        conn.exec_driver_sql(
            "INSERT INTO credentials_reusable VALUES(?,?,?,?,1,?,?,?,?,?,?)",
            (
                row["id"],
                row["scheme"],
                row["name"],
                row["auth_kind"],
                context,
                row["nonce"],
                row["ciphertext"],
                row["revision"],
                row["created_at"],
                row["updated_at"],
            ),
        )
    conn.exec_driver_sql("DROP TABLE credentials")
    conn.exec_driver_sql("ALTER TABLE credentials_reusable RENAME TO credentials")
    for definition in objects:
        conn.exec_driver_sql(definition)
    conn.exec_driver_sql("CREATE INDEX credentials_reference ON credentials(scheme,name)")


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
