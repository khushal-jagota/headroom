"""Upgrade existing wake identities, delivery members, indices, and sequence intact."""

import shutil
from contextlib import closing
from pathlib import Path

import pytest
from tests.unit.test_db import _migration_tree_through

from planner.core import db
from planner.measure_sources.resource_versions import SOURCE_TRIGGERS


def test_scalar_coverage_upgrade_adds_no_invented_facts_to_legacy_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.measures.contracts import ScalarCoverageStatus
    from planner.measures.data import read_observations

    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "goal_source_responsibility")
    with closing(db.connect(str(tmp_path / "before-scalar-coverage.db"))) as conn:
        db.create_schema(conn)
        conn.execute("""INSERT INTO measure_sources(id,name,project_id,account,responsible_kind,
            responsibility_revision,source_revision,created_at,updated_at)
            VALUES('source','Retained','project_vylo','fixture','chief',1,1,1,1)""")
        conn.execute("""INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,period,
            source_id,definition_revision,created_at,updated_at)
            VALUES('count','project_vylo','Retained','items','taken','period_count','UTC','day','source',1,1,1)""")
        conn.execute("""INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,
            source_revision,script_revision,script_sha256,frozen_at,required_outputs,created_at)
            VALUES('legacy','source','collection','manual','succeeded',1,1,'script',1,'[]',1)""")
        conn.execute("""INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,
            definition_revision) VALUES('count','2026-10-27',-2.5,'legacy',1,1)""")
        before = dict(conn.execute("SELECT * FROM measure_observations").fetchone())
        receipt = dict(conn.execute("SELECT * FROM measure_source_runs").fetchone())
        conn.commit()
        shutil.copy(
            shipped / "versions/scalar_observation_coverage.py",
            tree / "versions/scalar_observation_coverage.py",
        )
        db.create_schema(conn)
        after = dict(conn.execute("SELECT * FROM measure_observations").fetchone())
        assert after == {**before, "query_start_at": None, "query_end_at": None, "limitation": None}
        assert dict(conn.execute("SELECT * FROM measure_source_runs").fetchone()) == receipt
        observed = read_observations(conn, "count", "2026-10-27", "2026-10-27")[0]
        assert observed.value == -2.5 and observed.coverage_status is ScalarCoverageStatus.unknown
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("retain_notice", [True, False])
def test_automatic_measures_upgrade_preserves_existing_notice_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain_notice: bool,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "owner_message_preview")
    with closing(db.connect(str(tmp_path / "before-measures.db"))) as conn:
        db.create_schema(conn)
        conn.execute(
            "INSERT INTO manager_wakes(id,source_kind,source_revision,summary,created_at,"
            "target_kind,target_id) VALUES (7,'turn_failure',1,'retained',1,'chief','chief')"
        )
        conn.execute(
            "INSERT INTO manager_wake_batches(id,sender_message_id,message,status,created_at,"
            "updated_at,target_kind,target_id) VALUES (3,'retained',"
            "'retained','pending',1,1,'chief','chief')"
        )
        conn.execute("INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES (3,7)")
        conn.execute(
            "INSERT INTO manager_wakes(id,source_kind,source_revision,summary,created_at,"
            "target_kind,target_id) VALUES (100,'turn_failure',2,'deleted',1,'chief','chief')"
        )
        conn.execute("DELETE FROM manager_wakes WHERE id=100")
        if not retain_notice:
            conn.execute("DELETE FROM manager_wakes WHERE id=7")
        conn.execute(
            "CREATE TRIGGER fixture_wake_updated AFTER UPDATE ON manager_wakes BEGIN SELECT 1; END"
        )
        wake_row = conn.execute("SELECT * FROM manager_wakes WHERE id=7").fetchone()
        wake = None if wake_row is None else tuple(wake_row)
        batch = tuple(conn.execute("SELECT * FROM manager_wake_batches WHERE id=3").fetchone())
        objects = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='manager_wakes'")
        }
        conn.commit()
        shutil.copy(
            shipped / "versions/automatic_project_measures.py",
            tree / "versions/automatic_project_measures.py",
        )
        db.create_schema(conn)
        if retain_notice:
            assert wake is not None
            assert tuple(conn.execute("SELECT * FROM manager_wakes WHERE id=7").fetchone()) == (
                *wake,
                None,
                None,
                None,
            )
            assert tuple(conn.execute("SELECT * FROM manager_wake_batch_members").fetchone()) == (
                3,
                7,
            )
        else:
            assert conn.execute("SELECT * FROM manager_wakes").fetchall() == []
            assert conn.execute("SELECT * FROM manager_wake_batch_members").fetchall() == []
        assert (
            tuple(conn.execute("SELECT * FROM manager_wake_batches WHERE id=3").fetchone()) == batch
        )
        assert objects <= {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='manager_wakes'")
        }
        inserted = conn.execute(
            "INSERT INTO manager_wakes(source_kind,source_revision,summary,"
            "created_at,target_kind,target_id) "
            "VALUES ('turn_failure',3,'after',1,'chief','chief')"
        )
        assert inserted.lastrowid == 101
        triggers = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert {item.name for item in SOURCE_TRIGGERS} <= triggers
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_manual_measure_upgrade_preserves_sources_values_gaps_and_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "goal_conversations")
    with closing(db.connect(str(tmp_path / "before-manual.db"))) as conn:
        db.create_schema(conn)
        conn.execute("""INSERT INTO measure_sources(id,name,project_id,account,responsible_kind,
            responsibility_revision,source_revision,created_at,updated_at)
            VALUES('source','Retained','project_vylo','fixture','chief',1,1,1,1)""")
        conn.execute("""INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,
            source_id,definition_revision,created_at,updated_at)
            VALUES('taken','project_vylo','Retained','items','taken','reading','UTC','source',1,1,1)""")
        conn.execute("""INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,
            formula,definition_revision,created_at,updated_at)
            VALUES('assembled','project_other','Derived','items','assembled','reading','UTC',
            '{"op":"reference","measure_id":"taken","day_offset":0}',1,1,1)""")
        conn.execute("INSERT INTO measure_dependencies VALUES('assembled','taken')")
        conn.execute("""INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,
            source_revision,script_revision,script_sha256,frozen_at,required_outputs,created_at)
            VALUES('real-run','source','collection','manual','succeeded',1,1,'script',1,'[]',1)""")
        conn.execute("""INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,
            definition_revision) VALUES('taken','2026-10-27',-2.5,'real-run',1,1)""")
        conn.execute(
            "INSERT INTO measure_history_gaps "
            "VALUES('taken','2026-10-26','real-run','provider_has_no_value')"
        )
        conn.execute("CREATE INDEX fixture_measure_name ON measures(name)")
        conn.execute(
            "CREATE TRIGGER fixture_measure_observation AFTER UPDATE ON measure_observations "
            "BEGIN SELECT 1; END"
        )
        table_names = (
            "measures",
            "measure_observations",
            "measure_dependencies",
            "measure_source_runs",
            "measure_history_gaps",
        )
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in table_names
        }
        objects = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE tbl_name IN ('measures','measure_observations')"
            )
        }
        conn.commit()
        shutil.copy(shipped / "versions/manual_measures.py", tree / "versions/manual_measures.py")
        db.create_schema(conn)
        for table, rows in before.items():
            current = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            assert len(current) == len(rows)
            for old, new in zip(rows, current, strict=True):
                assert old == {key: new[key] for key in old}
        assert objects <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert {row[0] for row in conn.execute("SELECT write_revision FROM measures")} == {0}
        observation = conn.execute("SELECT * FROM measure_observations").fetchone()
        assert observation["manual_write_id"] is None and observation["run_id"] == "real-run"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # The restored original resource trigger still records source collection.
        before_version = conn.execute(
            "SELECT revision FROM resource_source_versions WHERE source_kind='automatic-measures'"
        ).fetchone()[0]
        conn.execute("UPDATE measure_observations SET value=-3.25 WHERE measure_id='taken'")
        assert (
            conn.execute(
                "SELECT revision FROM resource_source_versions "
                "WHERE source_kind='automatic-measures'"
            ).fetchone()[0]
            > before_version
        )


def test_reusable_credentials_preserve_legacy_ciphertext_and_explicit_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from planner.core.contracts import OWNER_PRINCIPAL, PlannerError
    from planner.credentials import actions, data
    from planner.credentials.contracts import BearerSecret, CredentialReference, CredentialScheme
    from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher

    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "primary_goal_checklist")
    with closing(db.connect(str(tmp_path / "before-reusable.db"))) as conn:
        db.create_schema(conn)
        cipher = FileCredentialCipher(tmp_path / "key", has_records=False)
        reference = CredentialReference(CredentialScheme.local, "same legacy name")
        retained: dict[str, tuple[bytes, bytes, bytes]] = {}
        for identifier, project, account, revision in (
            ("credential_a", "project_vylo", " Production Zürich ", 3),
            ("credential_b", "project_other", "Other account", 7),
        ):
            context = json.dumps(
                {
                    "project_id": project,
                    "account": account,
                    "scheme": "local",
                    "name": reference.name,
                    "auth_kind": "bearer",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            encrypted = cipher.encrypt(BearerSecret(identifier), associated_data=context)
            conn.execute(
                "INSERT INTO credentials VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project,
                    account,
                    "local",
                    reference.name,
                    "bearer",
                    encrypted.nonce,
                    encrypted.ciphertext,
                    revision,
                    11,
                    12,
                ),
            )
            retained[identifier] = (encrypted.nonce, encrypted.ciphertext, context)
        conn.execute("CREATE INDEX fixture_credential_name ON credentials(name)")
        conn.commit()
        shutil.copy(
            shipped / "versions/reusable_named_credentials.py",
            tree / "versions/reusable_named_credentials.py",
        )
        db.create_schema(conn)
        resolver = DatabaseCredentialResolver(conn, cipher)
        with pytest.raises(PlannerError, match="ambiguous") as ambiguity:
            resolver.resolve(reference)
        assert {row["id"] for row in ambiguity.value.detail["candidates"]} == set(retained)
        assert "ciphertext" not in json.dumps(ambiguity.value.detail)
        for identifier, (nonce, ciphertext, context) in retained.items():
            row = conn.execute("SELECT * FROM credentials WHERE id=?", (identifier,)).fetchone()
            assert (row["nonce"], row["ciphertext"], row["encryption_context"]) == (
                nonce,
                ciphertext,
                context,
            )
            assert row["encryption_version"] == 1
            assert (row["created_at"], row["updated_at"]) == (11, 12)
            assert cipher.decrypt(
                data.read_encrypted(conn, identifier),
                associated_data=data.read_encryption_context(conn, identifier).associated_data,
            ) == BearerSecret(identifier)
        assert data.read_metadata(conn, "credential_b").revision == 7
        replaced = actions.replace_credential(
            conn,
            "credential_b",
            BearerSecret("replacement"),
            expected_revision=7,
            principal=OWNER_PRINCIPAL,
            now=15,
            key_path=tmp_path / "key",
        )
        assert replaced.revision == 8
        before = conn.execute(
            "SELECT revision FROM resource_source_versions "
            "WHERE source_kind='automatic-measures' AND source_id='all'"
        ).fetchone()[0]
        renamed = actions.rename_credential(
            conn,
            "credential_a",
            "Panels_provider_production",
            expected_revision=3,
            principal=OWNER_PRINCIPAL,
            now=16,
            key_path=tmp_path / "key",
        )
        assert renamed.id == "credential_a" and renamed.revision == 4
        assert resolver.resolve(renamed.reference).secret == BearerSecret("credential_a")
        assert data.read_encryption_context(conn, renamed.id).version == 2
        assert (
            conn.execute(
                "SELECT revision FROM resource_source_versions "
                "WHERE source_kind='automatic-measures' AND source_id='all'"
            ).fetchone()[0]
            > before
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_key_list(credentials)").fetchall() == []
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='fixture_credential_name'"
            ).fetchone()
            is not None
        )


@pytest.mark.parametrize("retain_notice", [True, False])
def test_dataset_and_goal_source_upgrade_preserves_runtime_rows_and_notice_sequences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain_notice: bool,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "reusable_named_credentials")
    with closing(db.connect(str(tmp_path / "before-goal-sources.db"))) as conn:
        db.create_schema(conn)
        conn.execute("""INSERT INTO measure_sources(id,name,project_id,account,responsible_kind,
            responsibility_revision,source_revision,created_at,updated_at)
            VALUES('source','Retained','project_vylo','fixture','chief',1,1,1,1)""")
        conn.execute("""INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,
            source_revision,script_revision,script_sha256,frozen_at,required_outputs,created_at)
            VALUES('legacy-run','source','collection','manual','succeeded',1,1,'script',1,'[]',1)""")
        conn.execute("""INSERT INTO manager_wakes(id,source_kind,source_revision,summary,created_at,
            target_kind,target_id,measure_source_id,source_episode,source_responsibility_revision)
            VALUES(7,'source_failure',1,'retained',1,'chief','chief','source',1,1)""")
        conn.execute("""INSERT INTO manager_wake_batches(id,sender_message_id,message,status,
            created_at,updated_at,target_kind,target_id)
            VALUES(3,'retained','retained','pending',1,1,'chief','chief')""")
        conn.execute("INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES(3,7)")
        conn.execute("""INSERT INTO manager_wakes(id,source_kind,source_revision,summary,created_at,
            target_kind,target_id) VALUES(100,'turn_failure',2,'deleted',1,'chief','chief')""")
        conn.execute("DELETE FROM manager_wakes WHERE id=100")
        conn.execute("""INSERT INTO manager_wake_batches(id,sender_message_id,message,status,
            created_at,updated_at,target_kind,target_id)
            VALUES(200,'deleted','deleted','pending',1,1,'chief','chief')""")
        conn.execute("DELETE FROM manager_wake_batches WHERE id=200")
        if not retain_notice:
            conn.execute("DELETE FROM manager_wake_batches WHERE id=3")
            conn.execute("DELETE FROM manager_wakes WHERE id=7")
        conn.execute("CREATE INDEX fixture_source_name ON measure_sources(name)")
        conn.execute(
            "CREATE TRIGGER fixture_batch_update AFTER UPDATE ON manager_wake_batches "
            "BEGIN SELECT 1; END"
        )
        tables = (
            "measure_sources",
            "measure_source_runs",
            "manager_wakes",
            "manager_wake_batches",
            "manager_wake_batch_members",
        )
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables
        }
        objects = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name IN "
                "('measure_sources','manager_wakes','manager_wake_batches')"
            )
        }
        conn.commit()
        for migration in (
            "credential_conversation_requests.py",
            "dimensional_datasets.py",
            "goal_source_responsibility.py",
        ):
            shutil.copy(shipped / "versions" / migration, tree / "versions" / migration)
        db.create_schema(conn)
        for table, rows in before.items():
            current = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            assert len(current) == len(rows)
            for old, new in zip(rows, current, strict=True):
                assert old == {key: new[key] for key in old}
        assert objects <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert conn.execute("SELECT responsible_goal_id FROM measure_sources").fetchone()[0] is None
        from planner.measure_sources.data import read_run, read_run_detail

        legacy = read_run(conn, "legacy-run")
        assert legacy.dataset_outputs == ()
        assert legacy.dataset_result_count == legacy.dataset_row_count == 0
        assert read_run_detail(conn, "legacy-run").test_dataset_results is None
        inserted = conn.execute("""INSERT INTO manager_wakes(source_kind,source_revision,summary,
            created_at,target_kind,target_id) VALUES('turn_failure',3,'goal',1,'goal','goal')""")
        assert inserted.lastrowid == 101
        inserted = conn.execute("""INSERT INTO manager_wake_batches(
            sender_message_id,message,status,created_at,updated_at,target_kind,target_id)
            VALUES('goal','goal','pending',1,1,'goal','goal')""")
        assert inserted.lastrowid == 201
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
