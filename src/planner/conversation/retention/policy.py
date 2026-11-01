"""Versioned diagnostic expiry policy, inactive until explicit offline apply."""

POLICY_VERSION = "diagnostic-history-14-days-v1"
RETENTION_SECONDS = 14 * 24 * 60 * 60
SUPPORTED_SCHEMA_REVISIONS = frozenset(
    {
        "workspace_content_projections",
        "worker_server_ownership",
        "open_work_group_index",
        "diagnostic_expiry_metadata",
        "remove_calendar_records",
        "rename_managers",
        "notice_suppression_audit",
        "automatic_deployment_recovery",
        "review_messages",
        "review_work_cards",
        "owner_message_preview",
        "video_upload_ledger",
        "mac_vps_execution",
        # This workspace CHECK expansion leaves diagnostics and mac_runs unchanged.
        "executor_default_workspace",
        "automatic_project_measures",
        # Goal storage/history adds no changes to diagnostic event or view schemas.
        # Goal identities remain ineligible under the existing owner classifier.
        "goal_pages",
        "goal_conversations",
        # Manual measure provenance leaves diagnostic events and views unchanged.
        "manual_measures",
        # The goal checklist selector leaves diagnostic events and views unchanged.
        "primary_goal_checklist",
        # Credentials, datasets, and source responsibility leave diagnostic rows unchanged.
        "reusable_named_credentials",
        "credential_conversation_requests",
        "dimensional_datasets",
        "goal_source_responsibility",
        "scalar_observation_coverage",
        "goals_staging_reconciliation",
    }
)
DIAGNOSTIC_KINDS = frozenset(
    {
        "agent_message",
        "tool_call_started",
        "tool_call_finished",
        "plan_updated",
        "token_usage",
        "context_compacted",
    }
)

SUPPORTED_VIEW_TABLES = frozenset(
    {
        "conversation_view_state",
        "conversation_view_rows",
        "conversation_view_turns",
        "conversation_view_groups",
        "conversation_view_lookups",
    }
)
