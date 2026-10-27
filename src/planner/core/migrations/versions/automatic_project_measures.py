"""Store scoped source scripts, encrypted credentials, and dated measure inputs."""

from __future__ import annotations

import re

from alembic import op

revision = "automatic_project_measures"
down_revision = "owner_message_preview"
branch_labels = None
depends_on = None

_TABLES = (
    """CREATE TABLE credentials (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
      account TEXT NOT NULL, scheme TEXT NOT NULL CHECK(scheme='local'), name TEXT NOT NULL,
      auth_kind TEXT NOT NULL CHECK(auth_kind IN ('bearer','header','basic')),
      allowed_origins TEXT NOT NULL CHECK(json_valid(allowed_origins)),
      nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      UNIQUE(project_id,account,scheme,name))""",
    """CREATE TABLE measure_sources (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
      account TEXT NOT NULL,
      responsible_kind TEXT NOT NULL CHECK(responsible_kind IN ('chief','manager')),
      responsible_manager_id TEXT REFERENCES managers(id) ON DELETE RESTRICT,
      responsibility_revision INTEGER NOT NULL CHECK(responsibility_revision>0),
      repair_ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,
      enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)), local_time TEXT,
      source_revision INTEGER NOT NULL CHECK(source_revision>0),
      script_revision_counter INTEGER NOT NULL DEFAULT 0 CHECK(script_revision_counter>=0),
      active_script TEXT, active_script_revision INTEGER, active_script_sha256 TEXT,
      active_script_author_kind TEXT, active_script_author_id TEXT, active_script_created_at INTEGER,
      pending_script TEXT, pending_script_revision INTEGER, pending_script_sha256 TEXT,
      pending_script_author_kind TEXT, pending_script_author_id TEXT, pending_script_created_at INTEGER,
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      CHECK((responsible_kind='chief' AND responsible_manager_id IS NULL) OR
            (responsible_kind='manager' AND responsible_manager_id IS NOT NULL)),
      CHECK((active_script IS NULL AND active_script_revision IS NULL AND active_script_sha256 IS NULL
             AND active_script_author_kind IS NULL AND active_script_author_id IS NULL AND active_script_created_at IS NULL) OR
            (active_script IS NOT NULL AND active_script_revision IS NOT NULL AND active_script_sha256 IS NOT NULL
             AND active_script_author_kind IS NOT NULL AND active_script_author_id IS NOT NULL AND active_script_created_at IS NOT NULL)),
      CHECK((pending_script IS NULL AND pending_script_revision IS NULL AND pending_script_sha256 IS NULL
             AND pending_script_author_kind IS NULL AND pending_script_author_id IS NULL AND pending_script_created_at IS NULL) OR
            (pending_script IS NOT NULL AND pending_script_revision IS NOT NULL AND pending_script_sha256 IS NOT NULL
             AND pending_script_author_kind IS NOT NULL AND pending_script_author_id IS NOT NULL AND pending_script_created_at IS NOT NULL)),
      CHECK(enabled=0 OR (active_script IS NOT NULL AND local_time IS NOT NULL)))""",
    """CREATE TABLE measure_source_destinations (
      source_id TEXT NOT NULL REFERENCES measure_sources(id) ON DELETE CASCADE,
      grant_name TEXT NOT NULL, origin TEXT NOT NULL,
      method TEXT NOT NULL CHECK(method IN ('GET','POST')), path TEXT NOT NULL,
      path_match TEXT NOT NULL CHECK(path_match IN ('exact','prefix')),
      credential_id TEXT REFERENCES credentials(id) ON DELETE RESTRICT,
      PRIMARY KEY(source_id,grant_name))""",
    """CREATE TABLE measures (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
      unit TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('taken','assembled')),
      kind TEXT NOT NULL CHECK(kind IN ('reading','running_total','period_count')),
      timezone TEXT NOT NULL, period TEXT CHECK(period IN ('day','week','month')),
      source_id TEXT REFERENCES measure_sources(id) ON DELETE RESTRICT,
      formula TEXT CHECK(formula IS NULL OR json_valid(formula)),
      definition_revision INTEGER NOT NULL CHECK(definition_revision>0),
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      CHECK((origin='taken' AND source_id IS NOT NULL AND formula IS NULL) OR
            (origin='assembled' AND source_id IS NULL AND formula IS NOT NULL)),
      CHECK((kind='period_count' AND period IS NOT NULL) OR
            (kind!='period_count' AND period IS NULL)))""",
    """CREATE TABLE measure_dependencies (
      measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE CASCADE,
      input_measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE RESTRICT,
      PRIMARY KEY(measure_id,input_measure_id))""",
    """CREATE TABLE measure_source_runs (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES measure_sources(id) ON DELETE RESTRICT,
      mode TEXT NOT NULL CHECK(mode IN ('collection','test')),
      trigger TEXT NOT NULL CHECK(trigger IN ('scheduled','manual','test')),
      status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','interrupted')),
      source_revision INTEGER NOT NULL, script_revision INTEGER NOT NULL, script_sha256 TEXT NOT NULL,
      frozen_at INTEGER NOT NULL, required_outputs TEXT NOT NULL CHECK(json_valid(required_outputs)),
      history_first_date TEXT, history_last_date TEXT, schedule_date TEXT, schedule_time TEXT,
      created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER,
      failure TEXT CHECK(failure IS NULL OR json_valid(failure)),
      metrics TEXT CHECK(metrics IS NULL OR json_valid(metrics)),
      observation_count INTEGER NOT NULL DEFAULT 0 CHECK(observation_count>=0),
      test_candidates TEXT CHECK(test_candidates IS NULL OR json_valid(test_candidates)),
      CHECK((mode='test')=(trigger='test')),
      CHECK((trigger='scheduled' AND schedule_date IS NOT NULL AND schedule_time IS NOT NULL) OR
            (trigger!='scheduled' AND schedule_date IS NULL AND schedule_time IS NULL)),
      CHECK((history_first_date IS NULL)=(history_last_date IS NULL)),
      CHECK(test_candidates IS NULL OR (mode='test' AND status='succeeded')))""",
    """CREATE TABLE measure_observations (
      measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE RESTRICT, date TEXT NOT NULL,
      value REAL NOT NULL, run_id TEXT NOT NULL REFERENCES measure_source_runs(id) ON DELETE RESTRICT,
      observed_at INTEGER NOT NULL, definition_revision INTEGER NOT NULL,
      period_start_date TEXT, period_end_date_exclusive TEXT,
      CHECK((period_start_date IS NULL)=(period_end_date_exclusive IS NULL)),
      PRIMARY KEY(measure_id,date))""",
    """CREATE TABLE measure_history_gaps (
      measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE RESTRICT, date TEXT NOT NULL,
      run_id TEXT NOT NULL REFERENCES measure_source_runs(id) ON DELETE RESTRICT,
      reason TEXT NOT NULL CHECK(reason IN ('provider_has_no_history','provider_has_no_value')),
      PRIMARY KEY(measure_id,date))""",
    """CREATE TABLE measure_source_failure_episodes (
      source_id TEXT NOT NULL REFERENCES measure_sources(id) ON DELETE RESTRICT,
      episode INTEGER NOT NULL CHECK(episode>0), opened_at INTEGER NOT NULL,
      latest_run_id TEXT NOT NULL REFERENCES measure_source_runs(id) ON DELETE RESTRICT,
      closed_at INTEGER, PRIMARY KEY(source_id,episode))""",
)


def upgrade() -> None:
    conn = op.get_bind()
    for sql in _TABLES:
        conn.exec_driver_sql(sql)
    for sql in (
        "CREATE INDEX idx_measure_sources_slot ON measure_sources(enabled,local_time,id)",
        "CREATE INDEX idx_measure_sources_project ON measure_sources(project_id,id)",
        "CREATE INDEX idx_measures_source ON measures(source_id,id)",
        "CREATE INDEX idx_measures_project ON measures(project_id,id)",
        "CREATE INDEX idx_measure_dependency_input ON measure_dependencies(input_measure_id)",
        "CREATE INDEX idx_measure_runs_source ON measure_source_runs(source_id,created_at DESC,id)",
        "CREATE INDEX idx_measure_runs_pending ON measure_source_runs(created_at,id) WHERE status='pending'",
        "CREATE UNIQUE INDEX idx_measure_runs_slot ON measure_source_runs(source_id,schedule_date,schedule_time) WHERE trigger='scheduled'",
        "CREATE UNIQUE INDEX idx_measure_runs_running ON measure_source_runs(source_id) WHERE status='running'",
        "CREATE UNIQUE INDEX idx_measure_failure_open ON measure_source_failure_episodes(source_id) WHERE closed_at IS NULL",
    ):
        conn.exec_driver_sql(sql)

    # Preserve the table's complete current DDL and its explicit indices/triggers.
    # The migration runner disables FK enforcement for this rebuild and checks it afterwards.
    old_sequence = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='manager_wakes'"
    ).scalar_one_or_none()
    old_sql = str(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='manager_wakes'"
        ).scalar_one()
    )
    updated_sql = re.sub(
        r'^CREATE TABLE\s+"?manager_wakes"?', "CREATE TABLE manager_wakes_new", old_sql
    )
    updated_sql = updated_sql.replace("'turn_failure'", "'turn_failure','source_failure'", 1)
    if updated_sql == old_sql or "'source_failure'" not in updated_sql:
        raise RuntimeError("manager wake schema did not match the source extension")
    dependents = list(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name='manager_wakes' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL"
        )
    )
    conn.exec_driver_sql(updated_sql)
    conn.exec_driver_sql("INSERT INTO manager_wakes_new SELECT * FROM manager_wakes")
    conn.exec_driver_sql("DROP TABLE manager_wakes")
    conn.exec_driver_sql("ALTER TABLE manager_wakes_new RENAME TO manager_wakes")
    if old_sequence is not None:
        updated = conn.exec_driver_sql(
            "UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='manager_wakes'",
            (int(old_sequence),),
        )
        if updated.rowcount == 0:
            conn.exec_driver_sql(
                "INSERT INTO sqlite_sequence(name,seq) VALUES ('manager_wakes',?)",
                (int(old_sequence),),
            )
    for row in dependents:
        conn.exec_driver_sql(str(row[0]))
    conn.exec_driver_sql(
        "ALTER TABLE manager_wakes ADD COLUMN measure_source_id TEXT REFERENCES measure_sources(id) ON DELETE RESTRICT"
    )
    conn.exec_driver_sql("ALTER TABLE manager_wakes ADD COLUMN source_episode INTEGER")
    conn.exec_driver_sql(
        "ALTER TABLE manager_wakes ADD COLUMN source_responsibility_revision INTEGER"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX idx_manager_wakes_source_failure ON manager_wakes(measure_source_id,source_episode,source_responsibility_revision) WHERE source_kind='source_failure'"
    )

    for table in (
        "credentials",
        "measure_sources",
        "measure_source_destinations",
        "measures",
        "measure_dependencies",
        "measure_source_runs",
        "measure_observations",
        "measure_history_gaps",
        "measure_source_failure_episodes",
    ):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            conn.exec_driver_sql(
                f'CREATE TRIGGER "resource_measures_{table}_{operation.lower()}" '
                f"AFTER {operation} ON {table} BEGIN "
                "INSERT INTO resource_source_versions(source_kind,source_id,revision) "
                "VALUES ('automatic-measures','all',1) ON CONFLICT(source_kind,source_id) "
                "DO UPDATE SET revision=revision+1; END"
            )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
