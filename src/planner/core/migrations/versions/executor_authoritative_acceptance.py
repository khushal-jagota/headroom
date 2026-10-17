"""The authoritative executor receipts, callback evidence and file publications."""

from alembic import op

revision = "executor_authoritative_acceptance"
down_revision = "scheduled_ticket_locale"
branch_labels = None
depends_on = None

STATEMENTS = (
    """CREATE TABLE executor_connections (
        executor_incarnation TEXT PRIMARY KEY, connection_generation INTEGER NOT NULL,
        identity_json TEXT NOT NULL, connection_state TEXT NOT NULL,
        observed_at_ms INTEGER NOT NULL, lost_at_ms INTEGER,
        active INTEGER NOT NULL CHECK(active IN (0,1)))""",
    "CREATE UNIQUE INDEX executor_one_active_connection ON executor_connections(active) WHERE active=1",
    """CREATE TABLE executor_receipts (
        sender_id TEXT NOT NULL, submission_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>0),
        payload_sha256 TEXT NOT NULL, disposition TEXT NOT NULL CHECK(disposition IN ('accepted','refused')),
        result_json TEXT NOT NULL, accepted_at_vps_ms INTEGER NOT NULL,
        PRIMARY KEY(sender_id,submission_id), UNIQUE(sender_id,conversation_id,sequence))""",
    """CREATE TABLE executor_child_assignments (
        executor_incarnation TEXT NOT NULL, child_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        principal_kind TEXT NOT NULL, principal_id TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
        closed_at_ms INTEGER, PRIMARY KEY(executor_incarnation,child_id))""",
    """CREATE TABLE executor_asks (
        ask_key TEXT PRIMARY KEY, reference_json TEXT NOT NULL, option_ids_json TEXT NOT NULL,
        ask_kind TEXT NOT NULL DEFAULT 'permission' CHECK(ask_kind IN ('permission','user_input')),
        questions_json TEXT, user_input_answers_json TEXT,
        executor_incarnation TEXT NOT NULL, child_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        connection_generation INTEGER NOT NULL, reachability TEXT NOT NULL,
        observed_at_ms INTEGER NOT NULL,
        answer_id TEXT UNIQUE, option_id TEXT, accepted_at_vps_ms INTEGER,
        applied_at_mac_ms INTEGER, action_json TEXT, clock_sample_json TEXT,
        CHECK((ask_kind='permission' AND questions_json IS NULL)
           OR (ask_kind='user_input' AND questions_json IS NOT NULL)),
        CHECK((answer_id IS NULL AND option_id IS NULL AND accepted_at_vps_ms IS NULL
               AND user_input_answers_json IS NULL)
           OR (answer_id IS NOT NULL AND accepted_at_vps_ms IS NOT NULL
               AND ((ask_kind='permission' AND option_id IS NOT NULL AND user_input_answers_json IS NULL)
                    OR (ask_kind='user_input' AND option_id IS NULL AND user_input_answers_json IS NOT NULL)))))""",
    """CREATE TABLE executor_native_messages (
        sender_id TEXT NOT NULL, submission_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        principal_json TEXT NOT NULL, recipient_json TEXT NOT NULL, source_turn_json TEXT,
        state TEXT NOT NULL CHECK(state IN ('bound','dispatched','completed')),
        result_json TEXT, created_at_ms INTEGER NOT NULL,
        held_prompt_json TEXT, held_target_conversation_id TEXT, held_admission_order INTEGER,
        held_state TEXT CHECK(held_state IN ('pending','dispatching','consumed')),
        PRIMARY KEY(sender_id,submission_id),
        UNIQUE(held_target_conversation_id,held_admission_order))""",
    """CREATE TABLE executor_core_turns (
        conversation_id TEXT PRIMARY KEY, child_json TEXT NOT NULL,
        turn_json TEXT NOT NULL, runtime_json TEXT NOT NULL)""",
    """CREATE TABLE executor_command_outcomes (
        executor_incarnation TEXT NOT NULL, command_id TEXT NOT NULL,
        outcome_json TEXT NOT NULL, PRIMARY KEY(executor_incarnation,command_id))""",
    """CREATE TABLE executor_publications (
        publication_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('staged','accepted')),
        accepted_at_vps_ms INTEGER)""",
    """CREATE TABLE executor_publication_paths (
        scope TEXT NOT NULL, owner_id TEXT NOT NULL, relative_path TEXT NOT NULL,
        publication_id TEXT NOT NULL REFERENCES executor_publications(publication_id),
        PRIMARY KEY(scope,owner_id,relative_path))""",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.get_bind().exec_driver_sql(statement)


def downgrade() -> None:
    raise RuntimeError("Executor acceptance records are forward-only")
