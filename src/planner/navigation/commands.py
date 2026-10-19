"""Reviewed commands whose accepted work can affect navigation source facts.

This is a semantic inventory, independent of whether one invocation writes rows.
A complete capture with no source commit can certify a no-op.
"""

from __future__ import annotations

import re

from planner.navigation.command_contracts import CommandRoute


def _routes(method: str, reason: str, *paths: str) -> tuple[CommandRoute, ...]:
    return tuple(CommandRoute(method, "/api" + path, reason) for path in paths)


COMMAND_ROUTES = (
    *_routes(
        "POST",
        "Ticket creation, lifecycle, proposals and worker admission",
        "/tickets",
        "/tickets/{ticket_id}/restart-worker",
        "/tickets/{ticket_id}/propose",
        "/tickets/{ticket_id}/accept/{field}",
        "/tickets/{ticket_id}/reject",
        "/tickets/{ticket_id}/complete/{field}",
        "/tickets/{ticket_id}/request-help",
        "/tickets/{ticket_id}/backlog",
        "/tickets/{ticket_id}/resume",
        "/tickets/{ticket_id}/move",
    ),
    *_routes("PATCH", "Ticket navigation metadata", "/tickets/{ticket_id}"),
    *_routes("DELETE", "Ticket removal", "/tickets/{ticket_id}"),
    *_routes(
        "PUT",
        "Worker launch binding may change navigation source facts",
        "/tickets/{ticket_id}/employee-configuration",
    ),
    *_routes("POST", "Orchestrator and project creation", "/items", "/projects"),
    *_routes(
        "PATCH",
        "Orchestrator and project metadata",
        "/items/{item_id}",
        "/items/{item_id}/settled",
        "/projects/{project_id}",
    ),
    *_routes("DELETE", "Orchestrator removal", "/items/{item_id}"),
    *_routes(
        "PUT",
        "Navigation collection membership",
        "/collections/{collection}/{container_id}/{member_id}",
    ),
    *_routes(
        "DELETE",
        "Navigation collection membership",
        "/collections/{collection}/{container_id}/{member_id}",
    ),
    *_routes(
        "POST",
        "Conversation addresses and accepted lifecycle activity",
        "/chief/conversation/send",
        "/chief/conversation/reset",
        "/tickets/{ticket_id}/conversation/send",
        "/tickets/{ticket_id}/conversation/reset",
        "/items/{item_id}/conversation/send",
        "/items/{item_id}/conversation/reset",
        "/messages/send",
        "/conversation/conversations",
        "/conversation/conversations/{conversation_id}/send",
        "/conversation/conversations/{conversation_id}/owner-read",
        "/conversation/conversations/{conversation_id}/interrupt",
        "/conversation/conversations/{conversation_id}/kill",
        "/conversation/conversations/{conversation_id}/held-prompts/{held_prompt_id}/promote",
        "/conversation/conversations/{conversation_id}/permission-answers",
        "/conversation/conversations/{conversation_id}/user-input-answers",
    ),
    *_routes(
        "DELETE",
        "Accepted prompt lifecycle activity",
        "/conversation/conversations/{conversation_id}/held-prompts/{held_prompt_id}",
    ),
    *_routes("POST", "Worker definition registry", "/worker-types"),
    *_routes(
        "POST", "Corrections of existing navigation-affecting launch bindings",
        "/conversation/model-corrections/apply",
    ),
    *_routes(
        "PUT", "Worker definitions and launch bindings", "/workers/{worker_type}/launch-defaults"
    ),
    *_routes("PATCH", "Worker definition requirements", "/workers/{worker_type}/requirements"),
)

_MATCHERS = tuple(
    (route.method, re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", route.path) + "/?$"))
    for route in COMMAND_ROUTES
)


def is_navigation_command(method: str, path: str) -> bool:
    return any(method == verb and pattern.fullmatch(path) for verb, pattern in _MATCHERS)


# Exact exclusions make a new write route an explicit review decision. These
# operations do not change the current navigation source record contract.
EXCLUDED_COMMAND_ROUTES = (
    *_routes(
        "PUT",
        "Chief recap is dedicated content, outside navigation records",
        "/workers/chief-of-staff/recap",
    ),
    *_routes(
        "POST",
        "Feedback is outside navigation records",
        "/feedback",
        "/feedback/{feedback_id}/dismiss",
        "/feedback/{feedback_id}/reopen",
        "/feedback/use",
    ),
    *_routes(
        "PUT",
        "Notification configuration is outside navigation records",
        "/notifications/preferences/{subject_key}/{notification_type}",
    ),
    *_routes("POST", "Notification subscription configuration", "/notifications/subscriptions"),
    *_routes(
        "DELETE",
        "Notification subscription configuration",
        "/notifications/subscriptions/{subscription_id}",
    ),
    *_routes("POST", "Schedules create future work; no current navigation record", "/schedules"),
    *_routes("PATCH", "Schedule configuration", "/schedules/{schedule_id}"),
    *_routes("POST", "Ideas are outside navigation records", "/ideas"),
    *_routes(
        "POST",
        "Voice transcription returns text without conversation admission",
        "/conversation/voice-transcriptions",
    ),
    *_routes(
        "POST",
        "Backend catalogue, usage and login configuration",
        "/conversation/backends/refresh",
        "/conversation/model-corrections/preview",
        "/conversation/usage/refresh",
        "/conversation/backends/{backend_key}/update",
        "/conversation/backends/{backend_key}/sign-in",
    ),
    *_routes(
        "DELETE",
        "Backend login configuration",
        "/conversation/backends/{backend_key}/sign-in/{session_id}",
    ),
    *_routes(
        "PUT",
        "Backend model catalogue configuration",
        "/conversation/backends/{backend_key}/models/{model_id:path}/enablement",
    ),
    *_routes(
        "PATCH",
        "Skill text is not a navigation source fact",
        "/skills/{skill_name}",
        "/workers/{worker_type}/skill",
        "/workers/chief-of-staff/skill",
    ),
    *_routes(
        "PUT",
        "Skill text is not a navigation source fact",
        "/workers/{worker_type}/skill",
        "/workers/chief-of-staff/skill",
    ),
    CommandRoute(
        "PUT",
        "/files/sprint-items/{sprint_item_id}/{file_path:path}",
        "Managed files are outside navigation records",
    ),
    CommandRoute(
        "DELETE",
        "/files/sprint-items/{sprint_item_id}/{file_path:path}",
        "Managed files are outside navigation records",
    ),
)
