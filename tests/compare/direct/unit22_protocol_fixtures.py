"""Handwritten executor DTOs, reused across one complete wire exchange.

Each root is explicit. Nested fixtures come from the current protocol/app-transport
shapes. No schema field generates a fixture or selects a sample value.
"""

from __future__ import annotations

from typing import Any


def wire_fixtures() -> tuple[dict[str, Any], dict[str, str]]:
    identity = {
        "protocol_version": 22,
        "application_revision": "fixture",
        "spool_format_version": 5,
        "executor_incarnation": "11111111-1111-4111-8111-111111111111",
    }
    child = {
        "executor_incarnation": identity["executor_incarnation"],
        "child_id": "22222222-2222-4222-8222-222222222222",
        "conversation_id": "conversation",
    }
    turn = {"conversation_id": "conversation", "turn_number": 3}
    ask = {"child": child, "turn": turn, "ask_id": "ask", "kind": "permission"}
    answer = {"answer_id": "answer", "ask": ask, "option_id": "allow", "accepted_at_vps_ms": 100}
    manifest = {
        "scope": "ticket",
        "owner_id": "ticket",
        "relative_path": "artifact.txt",
        "sha256": "0" * 64,
        "byte_count": 3,
    }
    pieces = [
        {"text": "hello é"},
        {"stored_file_id": "image", "media_type": "image/png"},
        {
            "stored_file_id": "file",
            "media_type": "text/plain",
            "file_name": "artifact.txt",
            "byte_count": 3,
        },
    ]
    source = {"skill_name": "panels", "source_text": "Canonical α"}
    skills = {"sources": [source], "required_skill_names": ["panels"]}
    start = {
        "conversation_id": "conversation",
        "backend_key": "codex",
        "model": "fixture",
        "reasoning_effort": None,
        "access": "full",
        "workspace_folder": "/tmp/unit22",
        "role_materials": {
            "role_text": "Worker",
            "identity_environment_variables": [["PLAN_ACTOR", "ticket"]],
        },
    }
    prompt = {
        "content": pieces,
        "sender_content": pieces,
        "sender_label": "Owner",
        "mode": "queue",
        "kind": "write_prompt",
    }
    command = {
        "command_id": "prompt",
        "child": child,
        "operation": "write_prompt",
        "turn": turn,
        "arguments": prompt,
        "file_dependencies": [manifest],
    }
    backend_event = {
        "child": child,
        "method": "agent_message_delta",
        "turn": turn,
        "arguments": {"text_delta": "news"},
    }
    envelope = {
        "sender_id": "sender",
        "conversation_id": "conversation",
        "sequence": 1,
        "submission_id": "submission",
        "kind": "backend_event",
        "payload": backend_event,
        "file_dependencies": [manifest],
    }
    receipt = {
        "sender_id": "sender",
        "submission_id": "submission",
        "disposition": "accepted",
        "result": {"recorded": True},
        "accepted_at_vps_ms": 100,
    }
    outcome = {
        "command_id": "prompt",
        "child": child,
        "disposition": "succeeded",
        "result": {"kind": "prompt_accepted", "composed_content_delivered": True},
    }
    snapshot = {
        "backend_key": "codex",
        "installed": True,
        "executable_path": "/tmp/synthetic-codex",
        "version": "1",
        "identity": {"status": "authenticated", "account_label": "fixture"},
        "available_models": [
            {
                "model_id": "fixture",
                "display_name": "Fixture",
                "version": [1, 2],
                "reasoning_effort_options": ["low"],
            }
        ],
        "reasoning_effort_options": ["low"],
        "default_model_id": "fixture",
        "default_reasoning_effort": "low",
        "update_advisory": {
            "update_available": False,
            "latest_version": "1",
            "install_method": "npm_global",
            "update_command": ["fixture", "update"],
            "detail": "fixture",
        },
        "diagnoses": ["synthetic"],
        "models_complete": True,
    }
    model_request = {"backend_key": "codex", "refresh": False}
    mailbox = {
        "command_sequence": 1,
        "command_id": "prompt",
        "created_at_vps_ms": 100,
        "child_command": command,
    }
    rejected = {"command_id": "unknown", "reason": "unknown command"}
    command_result = {"command_id": "prompt", "child_outcome": outcome}
    waiting = {"conversation_id": "conversation", "oldest_sequence": 9}
    working = {"child": child, "turn": turn, "tool_running": True}
    quiescence = {
        "admission_stopped": True,
        "active_turn_count": 1,
        "pending_callback_count": 2,
        "uncertain_operation_count": 0,
        "unsent_entry_count": 1,
        "idle_children_stopped": False,
    }
    refusal = {
        "reason": "Mac needs update",
        "peer_identity": identity,
        "server_identity": identity,
        "refused_at_ms": 100,
    }
    update_target = {
        "request_id": "update",
        "application_revision": "fixture",
        "mode": "now",
        "activate": True,
        "archive_sha256": "1" * 64,
        "archive_size": 3,
        "archive_path": "/api/executor/update/update/archive",
    }
    prefetch = {
        "application_revision": "fixture",
        "archive_sha256": "1" * 64,
        "archive_size": 3,
        "archive_path": "/api/executor/update/prefetch/" + "1" * 64,
    }
    update_check = {"schema_version": 1, "target": update_target, "prefetch": prefetch}
    answer_value = {"question_id": "question", "answers": ["One", "Two"]}
    fixtures: dict[str, Any] = {
        "ExecutorIdentity": identity,
        "ChildReference": child,
        "TurnToken": turn,
        "AskReference": ask,
        "DurableAnswer": answer,
        "AnswerApplication": {"answer_id": "answer", "ask": ask, "applied_at_mac_ms": 110},
        "CanonicalSkillSource": source,
        "CanonicalSkillSnapshot": skills,
        "FileManifest": manifest,
        "FileSnapshot": {"manifest": manifest, "local_path": "/tmp/unit22/artifact.txt"},
        "FileUploadRequest": {"manifest": manifest, "child": child},
        "ResolvedConversationStart": start,
        "StartRun": {"resolved_start": start, "canonical_skill_snapshot": skills, "kind": "start"},
        "WritePrompt": prompt,
        "Steer": {"content": pieces, "sender_label": "Owner", "kind": "steer"},
        "CancelTurn": {"kind": "cancel_running_turn"},
        "StopRun": {"kind": "stop"},
        "AnswerPermission": {
            "ask_id": "ask",
            "option_id": "allow",
            "answer": answer,
            "kind": "answer_permission_ask",
        },
        "AnswerUserInput": {
            "request_id": "request",
            "answers": [answer_value],
            "kind": "answer_user_input",
        },
        "ChildCommand": command,
        "MailboxCommand": mailbox,
        "MailboxPollReply": {
            "commands": [
                mailbox,
                {
                    "command_sequence": 2,
                    "command_id": "models",
                    "created_at_vps_ms": 101,
                    "model_list_request": model_request,
                },
            ]
        },
        "ModelListRequest": model_request,
        "CommandOutcome": outcome,
        "CommandResult": command_result,
        "CommandOutcomeReport": {
            "outcomes": [command_result, {"command_id": "models", "model_list": snapshot}]
        },
        "RejectedCommandResult": rejected,
        "CommandOutcomeReply": {"recorded": ["prompt"], "rejected": [rejected]},
        "CliWrite": {
            "operation": "worker.propose",
            "arguments": {"text": "fixture", "optional": None},
            "child": child,
            "turn": turn,
        },
        "QueuedEnvelope": envelope,
        "AcceptanceReceipt": receipt,
        "ItemSubmission": {"envelope": envelope},
        "ItemBatch": {"sender_id": "sender", "items": [{"envelope": envelope}]},
        "ItemBatchStop": {
            "sequence": 2,
            "reason": "missing_files",
            "message": "upload first",
            "missing_files": [manifest],
        },
        "ItemBatchReply": {
            "receipts": [receipt],
            "stop": {"sequence": 2, "reason": "retry_later", "message": "retry"},
        },
        "BackendEvent": backend_event,
        "LiveEventBatch": {"events": [backend_event]},
        "LiveReadRequest": {"child": child, "path": "/api/tickets", "params": {"limit": 2}},
        "WaitingConversation": waiting,
        "WorkingTurn": working,
        "LeaseRequest": {
            "identity": identity,
            "process_id": 42,
            "mac_wall_clock_ms": 100,
            "queue_depth": 2,
            "oldest_item_age_ms": 5,
            "waiting_conversations": [waiting],
            "waiting_conversations_truncated": False,
            "working_turns": [working],
            "admission_stopped": False,
        },
        "LeaseReply": {
            "accepted_at_vps_ms": 100,
            "away_after_seconds": 60.0,
            "update": update_check,
            "mailbox_high_sequence": 2,
        },
        "ExecutorRefusal": refusal,
        "LeaseRefused": {
            "error": {
                "code": "executor_version_refused",
                "message": "update",
                "detail": {"reason": "fixture"},
            },
            "refusal": refusal,
            "update": update_check,
        },
        "ExecutorHealthRead": {
            "presence": "present",
            "since_ms": 100,
            "identity": identity,
            "last_contact_at_ms": 100,
            "refusal": None,
            "queue_depth": 2,
            "oldest_item_age_ms": 5,
            "admission_stopped": False,
        },
        "IpcRequest": {
            "request_id": "request",
            "operation": "read",
            "child_id": "child",
            "capability": "synthetic",
            "payload": {"path": "/api/tickets", "params": {}},
            "response_deadline": 12.5,
        },
        "IpcResponse": {
            "request_id": "request",
            "result": {
                "2": 1.0,
                "1": 1e-7,
                "recorded": True,
                "counts": [1, 1.0, 1.5, 9007199254740993, "1"],
            },
            "error": None,
        },
        "IpcRetryPayload": {"submission_id": "submission"},
        "IpcWritePayload": {"operation": "worker.propose", "arguments": {"text": "fixture"}},
        "UnconfirmedCliResult": {
            "request_id": "request",
            "submission_id": "submission",
            "publication": "queued",
        },
        "QuiesceRequest": {"exit_when_ready": True},
        "Quiescence": quiescence,
        "QuiescenceReceipt": {
            "identity": identity,
            "process_id": 42,
            "quiescence": quiescence,
            "exit_committed": False,
        },
        "MacLocalStatus": {
            "identity": identity,
            "process_id": 42,
            "lease_accepted_at_mac_ms": 100,
            "last_lease_failure": "synthetic",
            "refusal": refusal,
            "admission_stopped": False,
            "going_to_sleep": False,
        },
        "MacRuntimeManifest": {
            "schema_version": 1,
            "application_revision": "fixture",
            "platform": "darwin",
            "architecture": "arm64",
        },
        "MacRuntimeSelection": {
            "request_id": "update",
            "application_revision": "fixture",
            "runtime_path": "/tmp/unit22/runtime",
            "previous_runtime_path": None,
            "previous_revision": "old",
            "executor_incarnation": identity["executor_incarnation"],
            "connection_generation": 0,
            "archive_sha256": "1" * 64,
        },
        "MacUpdateCheck": update_check,
        "MacUpdatePrefetch": prefetch,
        "MacUpdateReport": {
            "request_id": "update",
            "application_revision": "fixture",
            "phase": "current",
            "identity": identity,
        },
        "MacUpdateStatus": {
            "request_id": "update",
            "target_revision": "fixture",
            "phase": "waiting",
        },
        "MacUpdateTarget": update_target,
        "BackendSnapshot": snapshot,
        "UserInputAnswer": answer_value,
    }
    events = {
        "AgentMessageDelta": ("agent_message_delta", {"text_delta": "news"}),
        "ModelThinkingHappened": ("model_thinking_happened", {}),
        "AgentMessageCompleted": ("agent_message_completed", {"content": pieces}),
        "CommandReplyReported": ("command_reply_reported", {"reply_text": "done"}),
        "ToolCallStarted": (
            "tool_call_started",
            {"tool_call_id": "tool", "title": "Read", "tool_kind": "read"},
        ),
        "ToolCallProgress": ("tool_call_progress", {"tool_call_id": "tool", "detail": "progress"}),
        "ToolCallFinished": (
            "tool_call_finished",
            {"tool_call_id": "tool", "tool_call_status": "completed"},
        ),
        "PlanUpdated": (
            "plan_updated",
            {"entries": [{"text": "Check wire", "status": "in_progress"}]},
        ),
        "TokenUsageReported": (
            "token_usage_reported",
            {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 3, "cost_usd": 0.25},
        ),
        "ContextCompacted": ("context_compacted", {}),
        "PermissionAskRaised": (
            "permission_ask_raised",
            {
                "ask": {
                    "ask_id": "ask",
                    "title": "Allow?",
                    "detail": None,
                    "options": [
                        {"option_id": "allow", "label": "Allow", "option_kind": "allow_once"}
                    ],
                }
            },
        ),
        "UserInputRequested": (
            "user_input_requested",
            {
                "request": {
                    "request_id": "request",
                    "questions": [
                        {
                            "question_id": "question",
                            "header": "Choice",
                            "question": "Choose",
                            "options": [{"label": "One", "description": "First"}],
                            "multi_select": True,
                            "allow_other": False,
                        }
                    ],
                }
            },
        ),
        "UserInputFailed": ("user_input_failed", {"request_id": "request", "detail": "expired"}),
        "TurnEnded": ("turn_ended", {"ending": "completed"}),
        "VendorSessionCursorRebound": (
            "vendor_session_cursor_rebound",
            {"vendor_session_cursor": "cursor"},
        ),
        "ComposerCatalogReported": (
            "composer_catalog_reported",
            {
                "composer_catalog": [
                    {
                        "kind": "command",
                        "display_text": "Help",
                        "insertion_text": "/help",
                        "description": "Help",
                    }
                ]
            },
        ),
        "BackgroundTaskStarted": (
            "background_task_started",
            {"task_id": "task", "task_description": "Work"},
        ),
        "BackgroundTaskEnded": (
            "background_task_ended",
            {"task_id": "task", "task_status": "completed"},
        ),
        "BackendStreamEnded": ("backend_stream_ended", {}),
    }
    fixtures.update({name: arguments for name, (_, arguments) in events.items()})
    return fixtures, {name: method for name, (method, _) in events.items()}
