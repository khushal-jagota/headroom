"""What the codex adapter has to be true about, against a codex that is scripted.

Every test here drives the real adapter over the real wire — a child process, pipes, and
newline-delimited JSON — with a scripted app-server on the other end. Two kinds of thing
are asserted: what the adapter reported to the core, and what actually reached the child,
read back out of the child's own transcript of what it was sent.

The obligations are the ones no outside observer can see. That the values a conversation
runs on reach the turn. That a resume which came back with somebody else's thread is
refused rather than accepted in silence. That thinking is dropped where it arrives. That an
ask still waiting when its turn dies is settled with codex instead of left hanging.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import pytest
from tests.unit.test_conversation_codex_scripted_app_server import scripted_app_server_launch

from planner.conversation.backends.codex_app_server import adapter
from planner.conversation.backends.codex_app_server.adapter import (
    WITHDRAWN_ASK_DECISION,
    CodexAppServerBackendChild,
    CodexAppServerBackendChildFactory,
    CodexChildLaunch,
)
from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendUserInputRequest,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationTurnEnding,
    PromptEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
    UserInputAnswer,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageFile,
    MessageImage,
    MessageText,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema


def _message_files() -> ConversationMessageFiles:
    """A file store for this exercise, under a database path of its own.

    Every adapter is handed one, because a message can carry a file and an adapter is
    what reads it. These exercises send words, so nothing is ever written here — but the
    adapter is built the way production builds it rather than with a hole where the file
    store goes.
    """
    return ConversationMessageFiles(str(Path(mkdtemp()) / "planner.db"))


ROLE_TEXT = "You are the worker on ticket t-1."
IDENTITY_VARIABLE = ("PANELS_IDENTITY_TICKET_ID", "t-1")

COMMAND_ITEM = {
    "type": "commandExecution",
    "id": "cmd-1",
    "command": "ls -la",
    "commandActions": [],
    "cwd": "/tmp",
    "status": "inProgress",
}
COMMAND_ITEM_DONE = {**COMMAND_ITEM, "status": "completed", "aggregatedOutput": "total 0\n"}
FILE_CHANGE_ITEM = {
    "type": "fileChange",
    "id": "fc-1",
    "status": "inProgress",
    "changes": [{"path": "/tmp/a.py", "kind": {"type": "update"}, "diff": "@@"}],
}


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 30.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


# --- starting --------------------------------------------------------------------------------


def test_a_fresh_conversation_shakes_hands_starts_a_thread_and_reports_its_cursor(
    tmp_path: Path,
) -> None:
    """The handshake, the thread, and everything the conversation runs on, at the child."""

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)

            assert scripted.sink.vendor_session_cursor == "thread-1"
            handshake = scripted.sent("initialize")
            assert handshake["params"]["clientInfo"]["name"] == "panels"
            assert handshake["params"]["capabilities"]["experimentalApi"] is True
            assert scripted.sent("initialized") is not None

            started = scripted.sent("thread/start")["params"]
            assert Path(started["cwd"]) == tmp_path
            assert started["model"] == "gpt-5.4-mini"
            assert started["developerInstructions"] == ROLE_TEXT
            assert started["approvalPolicy"] == "never"
            assert started["approvalsReviewer"] == "user"
            assert started["sandbox"] == "danger-full-access"

    _run(exercise)


def test_codex_publishes_only_the_native_built_ins_when_dynamic_sources_are_empty(
    tmp_path: Path,
) -> None:

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)

            assert [
                entry.insertion_text for entry in scripted.sink.composer_catalog_reports[-1]
            ] == [
                "/compact",
                "/review ",
                "/goal ",
            ]

    _run(exercise)


def test_codex_joins_paginated_metadata_to_callable_apps_and_enabled_plugins(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            "skills": {
                "data": [
                    {
                        "cwd": str(tmp_path),
                        "errors": [],
                        "skills": [
                            {
                                "name": "ship-it",
                                "description": "Ship this change",
                                "enabled": True,
                                "path": "/skills/ship-it/SKILL.md",
                                "scope": "user",
                            },
                            {
                                "name": "off",
                                "description": "Disabled",
                                "enabled": False,
                                "path": "/skills/off/SKILL.md",
                                "scope": "user",
                            },
                        ],
                    }
                ]
            },
            "installed_apps": {
                "apps": [
                    {"id": "demo", "runtimeName": "Demo", "enabled": True, "callable": True},
                    {"id": "idle", "runtimeName": "Idle", "enabled": True, "callable": False},
                ]
            },
            "apps": [
                {"data": [{"id": "idle", "name": "Idle", "isEnabled": True, "isAccessible": True}]},
                {
                    "data": [
                        {
                            "id": "demo",
                            "name": "Demo App",
                            "description": "Demo tools",
                            "isEnabled": True,
                            "isAccessible": True,
                        }
                    ]
                },
            ],
            "plugins": {
                "marketplaces": [
                    {
                        "name": "openai-curated-remote",
                        "plugins": [
                            _plugin(
                                "github",
                                enabled=True,
                                marketplace="openai-curated-remote",
                            ),
                            _plugin(
                                "disabled",
                                enabled=False,
                                marketplace="openai-curated-remote",
                            ),
                        ],
                    }
                ]
            },
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)

            entries = scripted.sink.composer_catalog_reports[-1]
            assert [(entry.kind.value, entry.insertion_text) for entry in entries] == [
                ("command", "/compact"),
                ("command", "/review "),
                ("command", "/goal "),
                ("app", "@demo-app "),
                ("plugin", "@github@openai-curated-remote "),
                ("skill", "$ship-it "),
            ]
            assert len(scripted.all_sent("app/list")) == 2

    _run(exercise)


def test_catalog_invocations_add_structured_inputs_without_rewriting_the_text(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            **_one_of_each_catalog(tmp_path),
            "turns": [{}, {}, {}],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            prompts = [
                "$ship-it release now",
                "@demo-app find updates",
                "@analytics@personal inspect usage",
            ]
            for turn_number, prompt in enumerate(prompts, 1):
                scripted.sink.expect_another_turn()
                await scripted.write_prompt(turn_number, text_message_content(prompt))
                await scripted.sink.wait_for_the_turn_to_end()

            inputs = [message["params"]["input"] for message in scripted.all_sent("turn/start")]
            assert inputs[0] == [
                {"type": "text", "text": prompts[0]},
                {"type": "skill", "name": "ship-it", "path": "/skills/ship-it/SKILL.md"},
            ]
            assert inputs[1] == [
                {"type": "text", "text": prompts[1]},
                {"type": "mention", "name": "Demo App", "path": "app://demo"},
            ]
            assert inputs[2] == [
                {"type": "text", "text": prompts[2]},
                {
                    "type": "mention",
                    "name": "Analytics",
                    "path": "plugin://analytics@personal",
                },
            ]

    _run(exercise)


def test_structured_skill_keeps_attachments_and_run_value_changes(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(
            tmp_path, script={**_one_of_each_catalog(tmp_path), "turns": [{}]}
        ) as scripted:
            await scripted.start(cursor=None)
            kept = await scripted.message_files.keep(
                "c", b"\x89PNG not really", media_type="image/png"
            )
            content = (
                MessageText(text="$ship-it inspect this"),
                MessageImage(stored_file_id=kept.stored_file_id, media_type="image/png"),
            )
            await scripted.child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change="gpt-5.6-codex",
                reasoning_effort_change="high",
            )
            turn = scripted.sent("turn/start")["params"]
            assert turn["model"] == "gpt-5.6-codex"
            assert turn["effort"] == "high"
            assert turn["input"][-1] == {
                "type": "skill",
                "name": "ship-it",
                "path": "/skills/ship-it/SKILL.md",
            }
            assert turn["input"][1] == {
                "type": "localImage",
                "path": str(kept.absolute_path),
            }

    _run(exercise)


@pytest.mark.parametrize(
    "prompt",
    [
        "/compact extra",
        "/goal nope",
        "/goal set",
        "/goal setter",
        "/goal clear extra",
    ],
)
def test_malformed_known_native_commands_refuse_without_a_codex_turn(
    tmp_path: Path, prompt: str
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed):
                await scripted.write_prompt(1, text_message_content(prompt))
            assert scripted.sent("turn/start", missing_is_none=True) is None

    _run(exercise)


@pytest.mark.parametrize("prompt", [" $unknown work", "before\n$unknown work"])
def test_reserved_tokens_away_from_absolute_message_start_remain_prose(
    tmp_path: Path, prompt: str
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={"turns": [{}]}) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content(prompt))
            await scripted.sink.wait_for_the_turn_to_end()
            assert scripted.sent("turn/start")["params"]["input"] == [
                {"type": "text", "text": prompt}
            ]

    _run(exercise)


@pytest.mark.parametrize(
    ("prompt_text", "expected_method"),
    [("$ship-it release now", "turn/start"), ("/compact", "thread/compact/start")],
)
def test_the_systems_composed_role_does_not_hide_a_first_prompt_catalog_invocation(
    tmp_path: Path, prompt_text: str, expected_method: str
) -> None:
    """Drive the real core and Codex child together across the first-prompt boundary."""

    async def exercise() -> None:
        database_path = tmp_path / "system.db"
        connection = connect(str(database_path))
        create_schema(connection)
        connection.close()
        transcript_path = tmp_path / "system-transcript.jsonl"
        script_path = tmp_path / "system-script.json"
        script_path.write_text(
            json.dumps(
                {
                    "transcript_path": str(transcript_path),
                    **_one_of_each_catalog(tmp_path),
                }
            ),
            encoding="utf-8",
        )
        argv, environment = scripted_app_server_launch(script_path)
        factory = CodexAppServerBackendChildFactory(
            CodexChildLaunch(argv=argv, environment_overrides=tuple(environment.items()))
        )
        store = ConversationStore(str(database_path))
        system = SqliteProcessConversationSystem(
            store=store,
            message_files=ConversationMessageFiles(str(database_path)),
            backend_child_factories={key: factory for key in ConversationBackendKey},
        )
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation_id="system-codex",
                    backend_key=ConversationBackendKey.codex,
                    model="gpt-5.4-mini",
                    reasoning_effort="medium",
                    role_materials=ConversationRoleMaterials(role_text=ROLE_TEXT),
                    workspace_folder=tmp_path,
                )
            )
            fate = await system.send(
                "system-codex",
                text_message_content(prompt_text),
                sender_label="owner",
            )
            assert fate == PromptDeliveryStarted()

            received = [
                entry["received"]
                for entry in (
                    json.loads(line)
                    for line in transcript_path.read_text(encoding="utf-8").splitlines()
                )
                if "received" in entry and entry["received"].get("method") == expected_method
            ][0]
            if expected_method == "turn/start":
                assert received["params"]["input"] == [
                    {"type": "text", "text": f"{ROLE_TEXT}\n\n{prompt_text}"},
                    {
                        "type": "skill",
                        "name": "ship-it",
                        "path": "/skills/ship-it/SKILL.md",
                    },
                ]
            else:
                assert received["params"] == {"threadId": "thread-1"}
            events = await store.read_events_after("system-codex", 0)
            prompt = next(
                event.payload for event in events if isinstance(event.payload, PromptEventPayload)
            )
            assert message_content_text(prompt.content) == prompt_text
        finally:
            await system.shutdown()

    _run(exercise)


def test_goal_command_records_one_durable_answer_and_completed_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        database_path = tmp_path / "goal-system.db"
        connection = connect(str(database_path))
        create_schema(connection)
        connection.close()
        transcript_path = tmp_path / "goal-system-transcript.jsonl"
        script_path = tmp_path / "goal-system-script.json"
        script_path.write_text(
            json.dumps({"transcript_path": str(transcript_path)}), encoding="utf-8"
        )
        argv, environment = scripted_app_server_launch(script_path)
        factory = CodexAppServerBackendChildFactory(
            CodexChildLaunch(argv=argv, environment_overrides=tuple(environment.items()))
        )
        store = ConversationStore(str(database_path))
        system = SqliteProcessConversationSystem(
            store=store,
            message_files=ConversationMessageFiles(str(database_path)),
            backend_child_factories={key: factory for key in ConversationBackendKey},
        )
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation_id="goal-system",
                    backend_key=ConversationBackendKey.codex,
                    model="gpt-5.4-mini",
                    workspace_folder=tmp_path,
                )
            )
            fate = await system.send(
                "goal-system",
                text_message_content("/goal set Keep the result durable"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryStarted()
            await system.wait_until_quiescent()
            events = await store.read_events_after("goal-system", 0)
            messages = [
                event.payload
                for event in events
                if isinstance(event.payload, AgentMessageEventPayload)
            ]
            endings = [
                event.payload
                for event in events
                if isinstance(event.payload, TurnEndedEventPayload)
            ]
            assert [message_content_text(message.content) for message in messages] == [
                "Goal set: Keep the result durable"
            ]
            assert [ending.ending for ending in endings] == [ConversationTurnEnding.completed]
            assert not [
                entry
                for entry in transcript_path.read_text(encoding="utf-8").splitlines()
                if '"method": "turn/start"' in entry
            ]
        finally:
            await system.shutdown()

    _run(exercise)


@pytest.mark.parametrize(
    ("prompt_text", "expected_method"),
    [("$ship-it retry", "turn/start"), ("/compact", "thread/compact/start")],
)
def test_a_persisted_cursor_without_a_delivered_prompt_keeps_first_prompt_dispatch(
    tmp_path: Path, prompt_text: str, expected_method: str
) -> None:
    """A restart keeps the core's sender/composed distinction independent of its cursor."""

    async def exercise() -> None:
        database_path = tmp_path / "restart.db"
        connection = connect(str(database_path))
        create_schema(connection)
        connection.close()
        store = ConversationStore(str(database_path))

        def build_system(
            name: str, script: dict[str, Any]
        ) -> tuple[SqliteProcessConversationSystem, Path]:
            transcript_path = tmp_path / f"{name}-transcript.jsonl"
            script_path = tmp_path / f"{name}-script.json"
            script_path.write_text(
                json.dumps({"transcript_path": str(transcript_path), **script}),
                encoding="utf-8",
            )
            argv, environment = scripted_app_server_launch(script_path)
            factory = CodexAppServerBackendChildFactory(
                CodexChildLaunch(argv=argv, environment_overrides=tuple(environment.items()))
            )
            return (
                SqliteProcessConversationSystem(
                    store=store,
                    message_files=ConversationMessageFiles(str(database_path)),
                    backend_child_factories={key: factory for key in ConversationBackendKey},
                ),
                transcript_path,
            )

        failed_script = _one_of_each_catalog(tmp_path)
        if expected_method == "turn/start":
            failed_script["turns"] = [{"respond": "error"}]
        else:
            failed_script["compact_response"] = "error"
        first, _ = build_system("first", failed_script)
        try:
            await first.start_conversation(
                ConversationStartRequest(
                    conversation_id="restart-codex",
                    backend_key=ConversationBackendKey.codex,
                    model="gpt-5.4-mini",
                    reasoning_effort="medium",
                    role_materials=ConversationRoleMaterials(role_text=ROLE_TEXT),
                    workspace_folder=tmp_path,
                )
            )
            refused = await first.send(
                "restart-codex", text_message_content(prompt_text), sender_label="owner"
            )
            assert isinstance(refused, PromptDeliveryRefused)
            await first.wait_until_quiescent()
            record = await store.read_conversation("restart-codex")
            assert record is not None and record.vendor_session_cursor == "thread-1"
            assert await store.has_delivered_prompt("restart-codex") is False
        finally:
            await first.shutdown()

        second, transcript_path = build_system(
            "second", {**_one_of_each_catalog(tmp_path), "turns": [{}]}
        )
        try:
            assert (
                await second.send(
                    "restart-codex", text_message_content(prompt_text), sender_label="owner"
                )
                == PromptDeliveryStarted()
            )
            received = [
                entry["received"]
                for entry in (
                    json.loads(line)
                    for line in transcript_path.read_text(encoding="utf-8").splitlines()
                )
                if "received" in entry and entry["received"].get("method") == expected_method
            ][0]
            if expected_method == "turn/start":
                assert received["params"]["input"][-1] == {
                    "type": "skill",
                    "name": "ship-it",
                    "path": "/skills/ship-it/SKILL.md",
                }
                assert received["params"]["input"][0] == {
                    "type": "text",
                    "text": f"{ROLE_TEXT}\n\n{prompt_text}",
                }
            else:
                assert received["params"] == {"threadId": "thread-1"}
        finally:
            await second.shutdown()

    _run(exercise)


def test_resume_replaces_the_persisted_catalog_before_structured_dispatch(tmp_path: Path) -> None:
    async def exercise() -> None:
        database_path = tmp_path / "catalog-resume.db"
        connection = connect(str(database_path))
        create_schema(connection)
        connection.close()
        store = ConversationStore(str(database_path))

        def build(name: str, skill: str) -> SqliteProcessConversationSystem:
            script_path = tmp_path / f"{name}.json"
            script_path.write_text(
                json.dumps(
                    {
                        "transcript_path": str(tmp_path / f"{name}.jsonl"),
                        "skills": _skills_response(tmp_path, skill),
                        "turns": [{}],
                    }
                ),
                encoding="utf-8",
            )
            argv, environment = scripted_app_server_launch(script_path)
            factory = CodexAppServerBackendChildFactory(
                CodexChildLaunch(argv=argv, environment_overrides=tuple(environment.items()))
            )
            return SqliteProcessConversationSystem(
                store=store,
                message_files=ConversationMessageFiles(str(database_path)),
                backend_child_factories={key: factory for key in ConversationBackendKey},
            )

        first = build("first-catalog", "stale")
        try:
            await first.start_conversation(
                ConversationStartRequest(
                    conversation_id="catalog-resume",
                    backend_key=ConversationBackendKey.codex,
                    model="gpt-5.4-mini",
                    workspace_folder=tmp_path,
                )
            )
            assert (
                await first.send(
                    "catalog-resume", text_message_content("prime catalog"), sender_label="owner"
                )
                == PromptDeliveryStarted()
            )
            await first.wait_until_quiescent()
            record = await _wait_for_stored_catalog(store, "catalog-resume", "$stale ")
            assert record is not None
            assert "$stale " in {entry.insertion_text for entry in record.composer_catalog}
        finally:
            await first.shutdown()

        second = build("second-catalog", "current")
        try:
            assert (
                await second.send(
                    "catalog-resume",
                    text_message_content("$current use the resumed catalogue"),
                    sender_label="owner",
                )
                == PromptDeliveryStarted()
            )
            await second.wait_until_quiescent()
            record = await _wait_for_stored_catalog(store, "catalog-resume", "$current ")
            assert record is not None
            tokens = {entry.insertion_text for entry in record.composer_catalog}
            assert "$current " in tokens
            assert "$stale " not in tokens
        finally:
            await second.shutdown()

    _run(exercise)


def test_colliding_tokens_get_stable_aliases_and_unknown_reserved_tokens_refuse(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        catalog = _one_of_each_catalog(tmp_path)
        duplicated = dict(catalog["skills"])
        duplicated["data"] = [
            *duplicated["data"],
            {
                "cwd": "/elsewhere",
                "errors": [],
                "skills": [
                    {
                        "name": "ship-it",
                        "description": "Other",
                        "enabled": True,
                        "path": "/other/SKILL.md",
                        "scope": "user",
                    }
                ],
            },
        ]
        catalog["skills"] = duplicated
        catalog["turns"] = [{}, {}]
        async with _scripted_child(tmp_path, script=catalog) as scripted:
            await scripted.start(cursor=None)
            aliases = sorted(
                entry.insertion_text.strip()
                for entry in scripted.sink.composer_catalog_reports[-1]
                if entry.kind is ComposerCatalogEntryKind.skill
            )
            assert len(aliases) == 2
            assert all(alias.startswith("$ship-it~") for alias in aliases)
            with pytest.raises(PromptWriteFailed, match="current Codex catalogue"):
                await scripted.write_prompt(1, text_message_content("$ship-it do it"))
            with pytest.raises(PromptWriteFailed, match="current Codex catalogue"):
                await scripted.write_prompt(1, text_message_content("@missing do it"))
            for number, prompt in enumerate((f"{aliases[0]} do it", "/unknown keep this"), 1):
                scripted.sink.expect_another_turn()
                await scripted.write_prompt(number, text_message_content(prompt))
                await scripted.sink.wait_for_the_turn_to_end()
            assert len(scripted.all_sent("turn/start")[0]["params"]["input"]) == 2
            assert len(scripted.all_sent("turn/start")[1]["params"]["input"]) == 1

    _run(exercise)


def test_collision_aliases_are_order_independent_and_exact_identities_deduplicate(
    tmp_path: Path,
) -> None:
    first = _skills_response(tmp_path, "ship-it")["data"][0]
    duplicate = dict(first)
    other = {
        "cwd": "/elsewhere",
        "errors": [],
        "skills": [
            {
                "name": "ship-it",
                "description": "Other",
                "enabled": True,
                "path": "/other/SKILL.md",
                "scope": "user",
            }
        ],
    }

    def aliases(data: list[dict[str, Any]]) -> dict[str, str | None]:
        skills = adapter.bindings.SkillsListResponse.model_validate({"data": data})
        snapshot = adapter._catalog_snapshot(skills, None, None, None)
        return {
            token: invocation.path
            for token, invocation in snapshot.invocations
            if invocation is not None and invocation.kind is ComposerCatalogEntryKind.skill
        }

    forward = aliases([first, duplicate, other])
    reverse = aliases([other, duplicate, first])
    assert forward == reverse
    assert len(forward) == 2
    assert set(forward.values()) == {"/skills/ship-it/SKILL.md", "/other/SKILL.md"}


def test_compact_and_review_use_native_methods_and_the_normal_turn_lifecycle(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script: dict[str, Any] = {
            "compact_response": "never",
            "turns": [{}, {"respond": "never"}],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("/compact"))
            await scripted.sink.wait_for_the_turn_to_end()
            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("/review focus on races"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sent("thread/compact/start")["params"] == {"threadId": "thread-1"}
            review = scripted.sent("review/start")["params"]
            assert review["delivery"] == "inline"
            assert review["target"] == {
                "type": "custom",
                "instructions": "focus on races",
            }
            assert scripted.sent("turn/start", missing_is_none=True) is None
            assert scripted.sink.endings == [
                ConversationTurnEnding.completed,
                ConversationTurnEnding.completed,
            ]

    _run(exercise)


def test_native_review_failure_is_a_refused_prompt_and_does_not_poison_the_next_turn(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script: dict[str, Any] = {
            "turns": [
                {"respond": "error", "message": "review unavailable"},
                {},
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed, match="review unavailable"):
                await scripted.write_prompt(1, text_message_content("/review"))

            await scripted.write_prompt(2, text_message_content("ordinary"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert scripted.sent("turn/start")["params"]["input"] == [
                {"type": "text", "text": "ordinary"}
            ]

    _run(exercise)


def test_goal_commands_use_exact_rpcs_and_synthetic_completed_turns(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            commands = (
                "/goal set   Ship the reliable command path   ",
                "/goal get   ",
                "/goal clear   ",
                "/goal",
                "/goal clear",
            )
            for turn_number, command in enumerate(commands, 1):
                if turn_number > 1:
                    scripted.sink.expect_another_turn()
                await scripted.write_prompt(turn_number, text_message_content(command))
                await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sent("thread/goal/set")["params"] == {
                "threadId": "thread-1",
                "objective": "Ship the reliable command path",
                "status": "active",
            }
            assert len(scripted.all_sent("thread/goal/get")) == 2
            assert len(scripted.all_sent("thread/goal/clear")) == 2
            assert scripted.sent("turn/start", missing_is_none=True) is None
            assert scripted.sink.agent_message_texts == [
                "Goal set: Ship the reliable command path",
                "Goal: Ship the reliable command path (active, 0 tokens, 0s)",
                "Goal cleared.",
                "No goal is set.",
                "No goal was set.",
            ]
            assert scripted.sink.endings == [ConversationTurnEnding.completed] * 5

    _run(exercise)


@pytest.mark.parametrize(
    "script",
    [
        {"goal_get": "error"},
        {"goal_get": {"goal": {"objective": "missing required fields"}}},
        {"ephemeral": True},
    ],
)
def test_goal_rpc_validation_and_ephemeral_threads_refuse(
    tmp_path: Path, script: dict[str, Any]
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed):
                await scripted.write_prompt(1, text_message_content("/goal"))
            assert scripted.sink.agent_message_texts == []
            assert scripted.sink.endings == []
            assert scripted.sent("turn/start", missing_is_none=True) is None

    _run(exercise)


def test_a_native_command_with_a_model_change_is_refused(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed, match="cannot change the model"):
                await scripted.write_prompt(
                    1, text_message_content("/compact"), model="gpt-5.6-codex"
                )
            assert scripted.sent("turn/start", missing_is_none=True) is None
            assert scripted.sent("thread/compact/start", missing_is_none=True) is None

    _run(exercise)


@pytest.mark.parametrize("prompt", ["/compact", "/review", "/goal"])
def test_native_commands_with_attachments_are_refused(tmp_path: Path, prompt: str) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            content = (
                MessageText(text=prompt),
                MessageImage(stored_file_id="unused", media_type="image/png"),
            )
            with pytest.raises(PromptWriteFailed, match="cannot carry attachments"):
                await scripted.child.write_prompt(
                    TurnToken(conversation_id="c", turn_number=1),
                    content,
                    sender_content=content,
                    sender_label="owner",
                    mode=PromptDeliveryMode.run_when_free,
                    model_change=None,
                    reasoning_effort_change=None,
                )
            assert scripted.sent("turn/start", missing_is_none=True) is None

    _run(exercise)


def test_catalog_invalidations_refresh_in_the_background_and_drop_failed_sources(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        first = _skills_response(tmp_path, "first")
        second = _skills_response(tmp_path, "second")
        script = {
            "skills": [first, second, "error"],
            "turns": [
                {
                    "actions": [
                        {"do": "skills_changed"},
                        {"do": "skills_changed"},
                        {"do": "complete", "status": "completed"},
                    ]
                },
                {"actions": [{"do": "apps_changed"}, {"do": "complete", "status": "completed"}]},
            ],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("ordinary"))
            await scripted.sink.wait_for_the_turn_to_end()
            await _wait_for_catalog_reports(scripted.sink, 2)
            assert "$second " in {
                entry.insertion_text for entry in scripted.sink.composer_catalog_reports[-1]
            }
            assert len(scripted.all_sent("skills/list")) == 2

            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("ordinary again"))
            await scripted.sink.wait_for_the_turn_to_end()
            await asyncio.sleep(0.15)
            assert len(scripted.sink.composer_catalog_reports) == 3
            assert all(
                entry.kind is not ComposerCatalogEntryKind.skill
                for entry in scripted.sink.composer_catalog_reports[-1]
            )

    _run(exercise)


@pytest.mark.parametrize("reported_error", ["skill", "plugin"])
def test_reported_catalog_entry_errors_keep_valid_sibling_sources(
    tmp_path: Path, reported_error: str
) -> None:
    async def exercise() -> None:
        good_skills = _skills_response(tmp_path, "first")
        bad_skills = {
            "data": [
                {
                    "cwd": str(tmp_path),
                    "skills": [
                        {
                            "name": "valid",
                            "description": "Still valid",
                            "enabled": True,
                            "path": "/skills/valid/SKILL.md",
                            "scope": "user",
                        }
                    ],
                    "errors": [{"path": "/broken/SKILL.md", "message": "cannot read"}],
                }
            ]
        }
        good_plugins: dict[str, Any] = {"marketplaces": [], "marketplaceLoadErrors": []}
        bad_plugins = {
            "marketplaces": [
                {"name": "personal", "plugins": [_plugin("valid-plugin", enabled=True)]}
            ],
            "marketplaceLoadErrors": [
                {"marketplacePath": "/broken/marketplace.json", "message": "invalid"}
            ],
        }
        script = {
            "skills": [good_skills, bad_skills] if reported_error == "skill" else good_skills,
            "plugins": [good_plugins, bad_plugins] if reported_error == "plugin" else good_plugins,
            "turns": [
                {
                    "actions": [
                        {"do": "skills_changed"},
                        {"do": "complete", "status": "completed"},
                    ]
                },
                {},
            ],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("refresh"))
            await scripted.sink.wait_for_the_turn_to_end()
            await asyncio.sleep(0.15)
            assert len(scripted.sink.composer_catalog_reports) == 2

            scripted.sink.expect_another_turn()
            if reported_error == "skill":
                with pytest.raises(PromptWriteFailed, match="current Codex catalogue"):
                    await scripted.write_prompt(2, text_message_content("$first continue"))
                assert "$valid " in {
                    entry.insertion_text for entry in scripted.sink.composer_catalog_reports[-1]
                }
            else:
                assert "@valid-plugin@personal " in {
                    entry.insertion_text for entry in scripted.sink.composer_catalog_reports[-1]
                }
                await scripted.write_prompt(2, text_message_content("$first continue"))
                await scripted.sink.wait_for_the_turn_to_end()
                assert scripted.all_sent("turn/start")[-1]["params"]["input"][-1] == {
                    "type": "skill",
                    "name": "first",
                    "path": "/skills/first/SKILL.md",
                }

    _run(exercise)


def test_startup_and_notification_refreshes_publish_in_request_order(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "skills": [
                _skills_response(tmp_path, "startup"),
                _skills_response(tmp_path, "notification"),
            ],
            "notify_apps_during_list": True,
            "app_list_response_delay": 0.1,
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await _wait_for_catalog_reports(scripted.sink, 2)

            skill_tokens = [
                [
                    entry.insertion_text
                    for entry in report
                    if entry.kind is ComposerCatalogEntryKind.skill
                ]
                for report in scripted.sink.composer_catalog_reports
            ]
            assert skill_tokens == [["$startup "], ["$notification "]]

    _run(exercise)


@pytest.mark.parametrize(
    ("failed_source", "missing_kinds"),
    [
        ("skills", {ComposerCatalogEntryKind.skill}),
        ("installed_apps", {ComposerCatalogEntryKind.app}),
        ("apps", {ComposerCatalogEntryKind.app}),
        ("plugins", {ComposerCatalogEntryKind.plugin}),
    ],
)
def test_each_catalog_source_failure_publishes_all_fresh_sibling_sources(
    tmp_path: Path,
    failed_source: str,
    missing_kinds: set[ComposerCatalogEntryKind],
) -> None:
    async def exercise() -> None:
        script = _one_of_each_catalog(tmp_path)
        script[failed_source] = "error"
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            entries = scripted.sink.composer_catalog_reports[-1]
            kinds = {entry.kind for entry in entries}
            assert ComposerCatalogEntryKind.command in kinds
            assert not (kinds & missing_kinds)
            if failed_source != "skills":
                assert ComposerCatalogEntryKind.skill in kinds
            if failed_source != "plugins":
                assert ComposerCatalogEntryKind.plugin in kinds

    _run(exercise)


def test_a_later_app_page_failure_publishes_no_partial_apps(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = _one_of_each_catalog(tmp_path)
        script["apps"] = [
            {
                "data": [
                    {
                        "id": "demo",
                        "name": "Demo App",
                        "isEnabled": True,
                        "isAccessible": True,
                    }
                ]
            },
            "error",
        ]
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            entries = scripted.sink.composer_catalog_reports[-1]
            assert all(entry.kind is not ComposerCatalogEntryKind.app for entry in entries)
            assert ComposerCatalogEntryKind.skill in {entry.kind for entry in entries}
            assert ComposerCatalogEntryKind.plugin in {entry.kind for entry in entries}

    _run(exercise)


def test_a_catalog_refresh_that_never_answers_does_not_block_turn_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(
            "planner.conversation.backends.codex_app_server.adapter.CATALOG_REQUEST_TIMEOUT_SECONDS",
            0.05,
        )
        script = {
            "skills": [_skills_response(tmp_path, "first"), "never"],
            "turns": [
                {
                    "actions": [
                        {"do": "skills_changed"},
                        {"do": "sleep", "seconds": 0.1},
                        {"do": "complete", "status": "completed"},
                    ]
                },
                {},
            ],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("ordinary"))
            await asyncio.wait_for(scripted.sink.wait_for_the_turn_to_end(), 0.5)
            assert scripted.sink.endings == [ConversationTurnEnding.completed]
            await asyncio.sleep(0.1)

            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("wire still works"))
            await scripted.sink.wait_for_the_turn_to_end()
            assert scripted.sink.endings == [
                ConversationTurnEnding.completed,
                ConversationTurnEnding.completed,
            ]

    _run(exercise)


def test_the_identity_and_the_workspace_folder_reach_the_child_process(tmp_path: Path) -> None:
    """Read out of the child's own account of how it was launched, not out of the adapter."""

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)

            launched = scripted.launched()
            assert Path(launched["cwd"]) == tmp_path.resolve()
            assert launched["environment"][IDENTITY_VARIABLE[0]] == IDENTITY_VARIABLE[1]
            # Codex has one home and one account, and this does not go looking for another.
            assert "CODEX_HOME" not in launched["environment"]

    _run(exercise)


def test_a_cursor_resumes_that_thread_and_mints_no_new_one(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor="thread-earlier")

            assert scripted.sent("thread/resume")["params"]["threadId"] == "thread-earlier"
            assert scripted.sent("thread/start", missing_is_none=True) is None
            assert scripted.sent("app/installed")["params"]["threadId"] == "thread-earlier"
            assert scripted.sink.composer_catalog_reports
            # Nothing was minted, so nothing is rebound: the cursor the core holds still
            # names the thread this conversation lives in.
            assert scripted.sink.vendor_session_cursor is None

    _run(exercise)


def test_a_resumed_thread_never_treats_user_authored_role_text_as_a_core_envelope(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(
            tmp_path, script={**_one_of_each_catalog(tmp_path), "turns": [{}]}
        ) as scripted:
            await scripted.start(cursor="thread-earlier")
            user_text = f"{ROLE_TEXT}\n\n$ship-it keep this ordinary"
            await scripted.write_prompt(1, text_message_content(user_text))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sent("turn/start")["params"]["input"] == [
                {"type": "text", "text": user_text}
            ]

    _run(exercise)


def test_a_resume_that_would_not_load_is_surfaced(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {"resume": {"outcome": "error", "message": "no such thread"}}
        async with _scripted_child(tmp_path, script=script) as scripted:
            with pytest.raises(SessionLoadFailed):
                await scripted.start(cursor="thread-earlier")

    _run(exercise)


def test_a_resume_that_came_back_with_another_thread_is_refused(tmp_path: Path) -> None:
    """The documented codex trap: a resume that quietly hands back a different thread.

    Downstream, a fresh thread and a restored one look identical — same shape, same wire,
    an agent that answers. The only place the difference is visible is here, so this is
    where it has to be caught.
    """

    async def exercise() -> None:
        script = {"resume": {"outcome": "other_thread", "thread_id": "thread-somebody-else"}}
        async with _scripted_child(tmp_path, script=script) as scripted:
            with pytest.raises(SessionLoadFailed) as refused:
                await scripted.start(cursor="thread-earlier")
            assert "thread-somebody-else" in str(refused.value)

    _run(exercise)


def test_a_child_that_will_not_spawn_says_so(tmp_path: Path) -> None:
    async def exercise() -> None:
        child = CodexAppServerBackendChild(
            launch=CodexChildLaunch(argv=("/nonexistent/codex", "app-server")),
            resolved_start=_resolved_start(tmp_path),
            event_sink=_RecordingSink(),
            message_files=_message_files(),
        )
        with pytest.raises(BackendSpawnFailed):
            await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)

    _run(exercise)


def test_a_child_that_will_not_shake_hands_says_it_did_not_start(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={"initialize": "error"}) as scripted:
            with pytest.raises(BackendSpawnFailed):
                await scripted.start(cursor=None)

    _run(exercise)


# --- a turn ------------------------------------------------------------------------------------


def test_a_turn_carries_the_text_the_values_and_the_access_posture(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("hello"))
            await scripted.sink.wait_for_the_turn_to_end()

            turn = scripted.sent("turn/start")["params"]
            assert turn["threadId"] == "thread-1"
            assert turn["input"] == [{"type": "text", "text": "hello"}]
            assert turn["model"] == "gpt-5.4-mini"
            assert turn["effort"] == "medium"
            assert turn["approvalPolicy"] == "never"
            assert turn["approvalsReviewer"] == "user"
            assert turn["sandboxPolicy"] == {"type": "dangerFullAccess"}

    _run(exercise)


def test_what_the_agent_says_becomes_deltas_then_one_finished_message(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "agent_delta", "item_id": "m1", "text": "he"},
                        {"do": "reasoning_delta", "text": "I am thinking about it"},
                        {"do": "agent_delta", "item_id": "m1", "text": "llo"},
                        {
                            "do": "item_completed",
                            "item": {"type": "agentMessage", "id": "m1", "text": "hello"},
                        },
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("say hello"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.deltas == ["he", "llo"]
            assert scripted.sink.agent_message_texts == ["hello"]
            assert scripted.sink.endings == [ConversationTurnEnding.completed]

    _run(exercise)


def test_thinking_is_dropped_where_it_arrives_and_only_its_arrival_is_told(
    tmp_path: Path,
) -> None:
    """Reasoning has no row and no text anywhere: only that it happened is passed on."""

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "reasoning_delta", "text": "first I will"},
                        {"do": "reasoning_delta", "text": " look around"},
                        # Codex streams reasoning two ways; both mean the same thing here.
                        {"do": "reasoning_summary_delta", "text": "looking around"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("think"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.deltas == []
            assert scripted.sink.agent_message_texts == []
            # One pulse per delta the adapter saw, of either kind; the core decides how
            # often anyone hears about them.
            assert scripted.sink.thinking_pulses == 3

    _run(exercise)


def test_a_message_the_turn_ended_part_way_through_is_still_finished(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "agent_delta", "item_id": "m1", "text": "half a sen"},
                        {"do": "complete", "status": "interrupted"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("say something"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.agent_message_texts == ["half a sen"]
            assert scripted.sink.endings == [ConversationTurnEnding.interrupted]

    _run(exercise)


def test_the_work_codex_does_becomes_tool_calls(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "item_started", "item": COMMAND_ITEM},
                        {"do": "item_started", "item": FILE_CHANGE_ITEM},
                        {"do": "item_completed", "item": COMMAND_ITEM_DONE},
                        {
                            "do": "item_completed",
                            "item": {**FILE_CHANGE_ITEM, "status": "declined"},
                        },
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("do some work"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.tool_calls_started == [
                ("cmd-1", "ls -la", "execute"),
                ("fc-1", "Change /tmp/a.py", "edit"),
            ]
            assert scripted.sink.tool_calls_finished == [
                ("cmd-1", ToolCallStatus.completed),
                # A declined change is a change that did not happen, which is a failure.
                ("fc-1", ToolCallStatus.failed),
            ]

    _run(exercise)


def test_both_ways_codex_says_a_tool_call_is_getting_on_become_the_same_frame(
    tmp_path: Path,
) -> None:
    """A shell command writing output and an MCP call reporting progress read the same.

    Codex has two notifications for it, keyed by the item the call was started under, and
    a reader watching the call does not care which one it was.
    """

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "item_started", "item": COMMAND_ITEM},
                        {
                            "do": "command_output_delta",
                            "item_id": "cmd-1",
                            "text": "total 0\n",
                        },
                        {"do": "mcp_progress", "item_id": "cmd-1", "text": "halfway"},
                        {"do": "item_completed", "item": COMMAND_ITEM_DONE},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("do some work"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.tool_calls_progressed == [
                ("cmd-1", "total 0\n"),
                ("cmd-1", "halfway"),
            ]
            # The call still starts and finishes exactly once: progress is neither.
            assert scripted.sink.tool_calls_started == [("cmd-1", "ls -la", "execute")]
            assert scripted.sink.tool_calls_finished == [("cmd-1", ToolCallStatus.completed)]

    _run(exercise)


def test_the_turns_plan_comes_through_whole_in_this_systems_own_words(
    tmp_path: Path,
) -> None:
    """Codex sends the whole plan on every change, and words its middle state its own way."""

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {
                            "do": "plan_updated",
                            "plan": [
                                {"step": "read the code", "status": "completed"},
                                {"step": "write the thing", "status": "inProgress"},
                                {"step": "run the tests", "status": "pending"},
                            ],
                            "explanation": "prose about the plan, which is not the plan",
                        },
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("plan some work"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.plans == [
                [
                    ("read the code", "completed"),
                    ("write the thing", "in_progress"),
                    ("run the tests", "pending"),
                ]
            ]

    _run(exercise)


def test_what_the_work_has_cost_is_codexs_running_total_and_never_a_price(
    tmp_path: Path,
) -> None:
    """Codex counts two things; the running total is the one this passes on.

    ``last`` is the model request that has just answered, on its own. Every request is
    charged for the whole conversation it was sent, so requests cannot be added up into
    what the work cost — ``total`` is codex's own answer to that and is what goes through.
    The two are given different numbers here so that reporting the wrong one is visible.
    """

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {
                            "do": "token_usage",
                            "last": {
                                "inputTokens": 7,
                                "outputTokens": 3,
                                "cachedInputTokens": 1,
                            },
                            "total": {
                                "inputTokens": 900,
                                "outputTokens": 120,
                                "cachedInputTokens": 640,
                            },
                        },
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("do some work"))
            await scripted.sink.wait_for_the_turn_to_end()

            # Codex says nothing about money, and none is worked out here from one.
            assert scripted.sink.token_usages == [(900, 120, 640, None)]

    _run(exercise)


def test_a_count_codex_replays_for_a_turn_that_is_over_is_not_this_turns(
    tmp_path: Path,
) -> None:
    """Codex replays stored usage after a resume, naming turns that are long finished."""

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {
                            "do": "token_usage",
                            "turn_id": "turn-long-over",
                            "total": {"inputTokens": 11, "outputTokens": 22},
                        },
                        {
                            "do": "token_usage",
                            "total": {"inputTokens": 33, "outputTokens": 44},
                        },
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("do some work"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.token_usages == [(33, 44, 0, None)]

    _run(exercise)


def test_a_compaction_codex_did_on_its_own_is_told_once_and_is_not_a_tool_call(
    tmp_path: Path,
) -> None:
    """Codex sends the compaction as an item, started and then completed; one row is right."""

    async def exercise() -> None:
        compaction = {"type": "contextCompaction", "id": "cc-1"}
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "item_started", "item": compaction},
                        {"do": "item_completed", "item": compaction},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("keep going"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.compactions == 1
            assert scripted.sink.tool_calls_started == []
            assert scripted.sink.tool_calls_finished == []

    _run(exercise)


def test_a_turn_still_running_reports_nothing_finished_for_it(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "item_started", "item": COMMAND_ITEM},
                        {"do": "item_completed", "item": COMMAND_ITEM},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("do some work"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.tool_calls_finished == []

    _run(exercise)


def test_a_failed_turn_carries_what_went_wrong_and_the_childs_standard_error(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            "stderr": "codex says something went wrong\n",
            "turns": [
                {
                    "actions": [
                        {"do": "error_notification", "message": "the model refused"},
                        {
                            "do": "complete",
                            "status": "failed",
                            "error": {"message": "the turn failed", "additionalDetails": "429"},
                        },
                    ]
                }
            ],
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.endings == [ConversationTurnEnding.failed]
            assert scripted.sink.error_summaries == ["the turn failed: 429"]
            assert scripted.sink.standard_error_tails[0] is not None
            assert "something went wrong" in scripted.sink.standard_error_tails[0]

    _run(exercise)


def test_a_retryable_error_is_not_an_ending(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "error_notification", "message": "retrying", "will_retry": True},
                        {"do": "agent_delta", "item_id": "m1", "text": "recovered"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.endings == [ConversationTurnEnding.completed]

    _run(exercise)


def test_a_turn_codex_refuses_to_start_is_a_write_that_did_not_land(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {"turns": [{"respond": "error", "message": "the thread is busy"}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed):
                await scripted.write_prompt(1, text_message_content("go"))
            # Nothing started, so nothing ended: the core hears one refusal and no more.
            assert scripted.sink.endings == []

    _run(exercise)


def test_a_turn_is_accepted_on_its_notification_when_the_answer_is_slow(tmp_path: Path) -> None:
    """``turn/start`` has two ways of saying it was taken, and either one is enough."""

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "respond": "never",
                    "actions": [
                        {"do": "agent_delta", "item_id": "m1", "text": "working"},
                        {"do": "complete", "status": "completed"},
                    ],
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.endings == [ConversationTurnEnding.completed]

    _run(exercise)


def test_the_child_dying_mid_turn_ends_the_turn_as_a_failure(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {"actions": [{"do": "die", "stderr": "codex fell over\n", "code": 3}]},
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.endings == [ConversationTurnEnding.failed]
            assert scripted.sink.standard_error_tails[0] is not None
            assert "fell over" in scripted.sink.standard_error_tails[0]

    _run(exercise)


# --- interrupting ---------------------------------------------------------------------------


def test_an_interrupt_reaches_codex_and_its_outcome_is_the_turns_ending(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "await_interrupt"},
                        {"do": "complete", "status": "interrupted"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.child.cancel_running_turn()
            await scripted.sink.wait_for_the_turn_to_end()

            interrupt = scripted.sent("turn/interrupt")["params"]
            assert interrupt == {"threadId": "thread-1", "turnId": "turn-1"}
            assert scripted.sink.endings == [ConversationTurnEnding.interrupted]

    _run(exercise)


def test_a_cancel_returns_only_once_codex_says_the_turn_has_ended(tmp_path: Path) -> None:
    """The send-now race: the next turn must not be written into the middle of a cancel.

    ``turn/interrupt`` is acknowledged the instant codex reads it and means nothing yet.
    A send-now writes its message the moment the cancel returns, so a cancel that returned
    at the acknowledgment would put that message in front of a codex still winding the old
    turn down — and codex would take it as work to do after that turn rather than as the
    turn to run now. The script here holds the ending back deliberately.
    """

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "await_interrupt"},
                        {"do": "sleep", "seconds": 0.4},
                        {"do": "complete", "status": "interrupted"},
                    ]
                },
                {
                    "actions": [
                        {
                            "do": "item_completed",
                            "item": {"type": "agentMessage", "id": "m2", "text": "urgent"},
                        },
                        {"do": "complete", "status": "completed"},
                    ]
                },
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("something long"))

            await scripted.child.cancel_running_turn()
            # Codex's own account of the ending has arrived by the time this returned.
            assert scripted.sink.endings == [ConversationTurnEnding.interrupted]

            # What a send-now does next: write immediately, with no waiting of its own.
            scripted.sink.expect_another_turn()
            await scripted.write_prompt(2, text_message_content("urgent"))
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.agent_message_texts == ["urgent"]
            # And at the child: the second turn was asked for after the first had ended.
            assert scripted.what_happened_at_the_child() == [
                "received turn/start",
                "received turn/interrupt",
                "emitted turn/completed",
                "received turn/start",
                "emitted turn/completed",
            ]

    _run(exercise)


def test_a_cancel_a_codex_never_answers_gives_up_rather_than_wedging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait is bounded: a backend that goes quiet must not hold the conversation."""

    async def exercise() -> None:
        script = {"turns": [{"actions": [{"do": "await_interrupt"}]}, {}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("something long"))

            await scripted.child.cancel_running_turn()
            # Codex never said the turn ended, and the conversation carries on regardless.
            assert scripted.sink.endings == []

            await scripted.write_prompt(2, text_message_content("urgent"))
            await scripted.sink.wait_for_the_turn_to_end()

    monkeypatch.setattr(adapter, "CANCEL_SETTLING_TIMEOUT_SECONDS", 0.2)
    _run(exercise)


# --- changing what the conversation runs on ----------------------------------------------------


def test_a_carried_change_is_this_turns_parameters_and_the_conversations_values_after(
    tmp_path: Path,
) -> None:
    """One operation, atomically: codex takes the change as parameters of the turn itself."""

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("first"))
            await scripted.sink.wait_for_the_turn_to_end()

            scripted.sink.expect_another_turn()
            await scripted.write_prompt(
                2,
                text_message_content("second"),
                model="gpt-5.4",
                reasoning_effort="high",
            )
            await scripted.sink.wait_for_the_turn_to_end()

            scripted.sink.expect_another_turn()
            await scripted.write_prompt(3, text_message_content("third"))
            await scripted.sink.wait_for_the_turn_to_end()

            turns = scripted.all_sent("turn/start")
            assert [(turn["params"]["model"], turn["params"]["effort"]) for turn in turns] == [
                ("gpt-5.4-mini", "medium"),
                ("gpt-5.4", "high"),
                # The change stood, so the turn after it runs on the new values too.
                ("gpt-5.4", "high"),
            ]

    _run(exercise)


def test_a_change_whose_turn_codex_refused_does_not_stand(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {"turns": [{"respond": "error", "message": "no"}, {}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed):
                await scripted.write_prompt(
                    1,
                    text_message_content("first"),
                    model="gpt-5.4",
                    reasoning_effort="high",
                )

            await scripted.write_prompt(2, text_message_content("second"))
            await scripted.sink.wait_for_the_turn_to_end()

            second = scripted.all_sent("turn/start")[1]["params"]
            assert (second["model"], second["effort"]) == ("gpt-5.4-mini", "medium")

    _run(exercise)


# --- permission asks ---------------------------------------------------------------------------


def test_an_ask_is_raised_with_codexs_own_answers_and_the_chosen_one_goes_back(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "request_approval", "which": "command", "command": "rm -rf /tmp/x"},
                        {"do": "await_approval"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("delete it"))
            ask = await scripted.sink.wait_for_an_ask()

            assert ask.title == "Run rm -rf /tmp/x"
            assert [option.option_id for option in ask.options] == [
                "accept",
                "acceptForSession",
                "decline",
            ]

            await scripted.child.answer_permission_ask(ask.ask_id, "acceptForSession")
            await scripted.sink.wait_for_the_turn_to_end()

            answered = scripted.answers()[0]
            assert answered["id"] == "server-item/commandExecution/requestApproval"
            assert answered["result"] == {"decision": "acceptForSession"}

    _run(exercise)


def test_an_ask_still_waiting_when_its_turn_dies_is_settled_with_codex(tmp_path: Path) -> None:
    """No vendor call is left hanging, and no answer is invented for one either."""

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "request_approval", "which": "file"},
                        {"do": "complete", "status": "interrupted"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("change it"))
            await scripted.sink.wait_for_an_ask()
            await scripted.sink.wait_for_the_turn_to_end()
            await scripted.wait_for_an_answer()

            answered = scripted.answers()[0]
            assert answered["result"] == {"decision": WITHDRAWN_ASK_DECISION}

    _run(exercise)


def test_an_answer_codex_does_not_offer_never_reaches_the_wire(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "request_approval", "which": "command"},
                        {"do": "await_approval"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            ask = await scripted.sink.wait_for_an_ask()

            with pytest.raises(Exception, match="not an answer codex offers"):
                await scripted.child.answer_permission_ask(ask.ask_id, "sure-why-not")
            # The ask is still waiting, so the real answer still lands.
            await scripted.child.answer_permission_ask(ask.ask_id, "decline")
            await scripted.sink.wait_for_the_turn_to_end()

    _run(exercise)


def test_a_request_codex_makes_that_this_does_not_answer_is_refused(tmp_path: Path) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "ask_unknown"},
                        {"do": "await_approval"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()
            await scripted.wait_for_an_answer()

            refusal = scripted.answers()[0]
            assert refusal["error"]["code"] == -32601

    _run(exercise)


# --- user input -------------------------------------------------------------------------------


def test_codex_user_input_is_raised_separately_and_the_complete_answer_map_goes_back(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {
                            "do": "request_user_input",
                            "item_id": "input-7",
                            "questions": [
                                {
                                    "id": "framework",
                                    "header": "Framework",
                                    "question": "Which framework?",
                                    "options": [
                                        {
                                            "label": "Svelte",
                                            "description": "Use Svelte components",
                                        },
                                        {
                                            "label": "React",
                                            "description": "Use React components",
                                        },
                                    ],
                                    "isOther": True,
                                    "isSecret": False,
                                },
                                {
                                    "id": "notes",
                                    "header": "Notes",
                                    "question": "Anything else?",
                                    "options": None,
                                    "isOther": True,
                                    "isSecret": False,
                                },
                            ],
                        },
                        {"do": "await_server_request_answer"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("choose"))
            request = await scripted.sink.wait_for_user_input()

            assert request.request_id == "turn-1:input-7"
            assert [question.question_id for question in request.questions] == [
                "framework",
                "notes",
            ]
            assert [option.label for option in request.questions[0].options] == [
                "Svelte",
                "React",
            ]
            assert request.questions[0].options[0].description == "Use Svelte components"
            assert request.questions[0].multi_select is False
            assert request.questions[0].allow_other is True
            assert request.questions[1].options == ()
            assert request.questions[1].allow_other is True
            assert scripted.sink.asks == []

            await scripted.child.answer_user_input(
                request.request_id,
                (
                    UserInputAnswer(question_id="framework", answers=("Svelte",)),
                    UserInputAnswer(question_id="notes", answers=("Keep it compact",)),
                ),
            )
            await scripted.sink.wait_for_the_turn_to_end()

            answered = scripted.answers()[0]
            assert answered["id"] == "server-item/tool/requestUserInput"
            assert answered["result"] == {
                "answers": {
                    "framework": {"answers": ["Svelte"]},
                    "notes": {"answers": ["Keep it compact"]},
                }
            }

    _run(exercise)


def test_codex_user_input_still_waiting_when_the_turn_dies_is_settled(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {
                            "do": "request_user_input",
                            "questions": [
                                {
                                    "id": "choice",
                                    "header": "Choice",
                                    "question": "Choose one",
                                    "options": [
                                        {"label": "A", "description": "First choice"},
                                        {"label": "B", "description": "Second choice"},
                                    ],
                                }
                            ],
                        },
                        {"do": "complete", "status": "interrupted"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("choose"))
            await scripted.sink.wait_for_user_input()
            await scripted.sink.wait_for_the_turn_to_end()
            await scripted.wait_for_an_answer()

            assert scripted.answers()[0]["result"] == {"answers": {}}

    _run(exercise)


def test_malformed_codex_user_input_fails_visibly_without_becoming_permission(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {
                            "do": "request_user_input",
                            "questions": [
                                {
                                    "id": "secret",
                                    "header": "Secret",
                                    "question": "What is the token?",
                                    "options": None,
                                    "isOther": True,
                                    "isSecret": True,
                                }
                            ],
                        },
                        {"do": "await_server_request_answer"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("ask"))
            await scripted.sink.wait_for_user_input_failure()
            await scripted.sink.wait_for_the_turn_to_end()

            assert scripted.sink.user_inputs == []
            assert scripted.sink.asks == []
            assert "unsupported secret input" in scripted.sink.user_input_failures[0][1]
            assert scripted.answers()[0]["result"] == {"answers": {}}

    _run(exercise)


# --- what this backend cannot do ---------------------------------------------------------------


def test_codex_does_not_take_text_into_a_running_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            with pytest.raises(PromptWriteFailed):
                await scripted.child.steer(text_message_content("keep going"), sender_label="owner")

    _run(exercise)


# --- the wire itself -----------------------------------------------------------------------------


def test_a_notification_this_reads_that_will_not_decode_is_said_out_loud(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The silent swallow is the defect this refuses to have.

    A protocol that moved under us and an agent that said nothing look the same from
    outside. Naming the method in a warning is what tells them apart.
    """

    async def exercise() -> None:
        script = {
            "turns": [
                {
                    "actions": [
                        {"do": "unknown_notification"},
                        {"do": "undecodable_agent_delta", "item_id": "m1"},
                        {"do": "complete", "status": "completed"},
                    ]
                }
            ]
        }
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()

    with caplog.at_level(logging.DEBUG, logger="planner.conversation.backends.codex_app_server"):
        _run(exercise)

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("item/agentMessage/delta" in record.getMessage() for record in warnings)
    # The one nothing here reads is noise from a protocol far larger than this adapter uses.
    assert not any("mcpServer/startupStatus/updated" in record.getMessage() for record in warnings)
    assert any(
        "mcpServer/startupStatus/updated" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.DEBUG
    )


def test_the_childs_standard_error_is_kept_bounded(tmp_path: Path) -> None:
    """A stderr burst can neither fill the pipe nor grow without limit."""

    async def exercise() -> None:
        flood = "a codex log line that says very little\n" * 20_000
        async with _scripted_child(tmp_path, script={"stderr": flood}) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("go"))
            await scripted.sink.wait_for_the_turn_to_end()

            tail = scripted.child._client.standard_error_tail()
            assert tail is not None
            assert len(tail) <= 64 * 1024 + 100
            assert tail.endswith("says very little\n")

    _run(exercise)


# --- the scaffolding ------------------------------------------------------------------------------


def _plugin(plugin_name: str, *, enabled: bool, marketplace: str = "personal") -> dict[str, Any]:
    return {
        "authPolicy": "ON_USE",
        "enabled": enabled,
        "id": f"{plugin_name}@{marketplace}",
        "installPolicy": "AVAILABLE",
        "installed": True,
        "interface": {
            "capabilities": [],
            "displayName": plugin_name.title(),
            "shortDescription": f"Use {plugin_name.title()}",
            "screenshotUrls": [],
            "screenshots": [],
        },
        "name": plugin_name,
        "source": {"type": "local", "path": f"/plugins/{plugin_name}"},
    }


def _skills_response(workspace: Path, name: str) -> dict[str, Any]:
    return {
        "data": [
            {
                "cwd": str(workspace),
                "errors": [],
                "skills": [
                    {
                        "name": name,
                        "description": f"Use {name}",
                        "enabled": True,
                        "path": f"/skills/{name}/SKILL.md",
                        "scope": "user",
                    }
                ],
            }
        ]
    }


def _one_of_each_catalog(workspace: Path) -> dict[str, Any]:
    return {
        "skills": _skills_response(workspace, "ship-it"),
        "installed_apps": {
            "apps": [{"id": "demo", "runtimeName": "Demo", "enabled": True, "callable": True}]
        },
        "apps": {
            "data": [
                {
                    "id": "demo",
                    "name": "Demo App",
                    "isEnabled": True,
                    "isAccessible": True,
                }
            ]
        },
        "plugins": {
            "marketplaces": [{"name": "personal", "plugins": [_plugin("analytics", enabled=True)]}]
        },
    }


async def _wait_for_catalog_reports(sink: _RecordingSink, count: int) -> None:
    for _ in range(100):
        if len(sink.composer_catalog_reports) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"only {len(sink.composer_catalog_reports)} catalogue reports arrived")


async def _wait_for_stored_catalog(
    store: ConversationStore, conversation_id: str, token: str
) -> Any:
    for _ in range(100):
        record = await store.read_conversation(conversation_id)
        if record is not None and token in {
            entry.insertion_text for entry in record.composer_catalog
        }:
            return record
        await asyncio.sleep(0.01)
    raise AssertionError(f"{token!r} never reached the stored composer catalogue")


def _resolved_start(workspace: Path) -> ResolvedConversationStart:
    from planner.conversation.contracts import ConversationBackendKey

    return ResolvedConversationStart(
        conversation_id="c",
        backend_key=ConversationBackendKey.codex,
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        role_materials=ConversationRoleMaterials(
            role_text=ROLE_TEXT, identity_environment_variables=(IDENTITY_VARIABLE,)
        ),
        workspace_folder=workspace,
        access=ConversationAccess.full,
    )


class _RecordingSink:
    """Everything the adapter reported, for a test driving one child directly."""

    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.agent_contents: list[MessageContent] = []
        self.tool_calls_started: list[tuple[str, str, str]] = []
        self.tool_calls_progressed: list[tuple[str, str]] = []
        self.thinking_pulses: int = 0
        self.plans: list[list[tuple[str, str]]] = []
        self.token_usages: list[tuple[int | None, int | None, int | None, float | None]] = []
        self.compactions: int = 0
        self.tool_calls_finished: list[tuple[str, ToolCallStatus]] = []
        self.asks: list[BackendPermissionAsk] = []
        self.user_inputs: list[BackendUserInputRequest] = []
        self.user_input_failures: list[tuple[str, str]] = []
        self.endings: list[ConversationTurnEnding] = []
        self.error_summaries: list[str | None] = []
        self.standard_error_tails: list[str | None] = []
        self.vendor_session_cursor: str | None = None
        self.composer_catalog_reports: list[tuple[ComposerCatalogEntry, ...]] = []
        self._turn_over = asyncio.Event()
        self._an_ask = asyncio.Event()
        self._user_input = asyncio.Event()
        self._user_input_failure = asyncio.Event()

    def expect_another_turn(self) -> None:
        self._turn_over.clear()

    async def wait_for_the_turn_to_end(self) -> None:
        await self._turn_over.wait()

    async def wait_for_an_ask(self) -> BackendPermissionAsk:
        await self._an_ask.wait()
        self._an_ask.clear()
        return self.asks[-1]

    async def wait_for_user_input(self) -> BackendUserInputRequest:
        await self._user_input.wait()
        self._user_input.clear()
        return self.user_inputs[-1]

    async def wait_for_user_input_failure(self) -> tuple[str, str]:
        await self._user_input_failure.wait()
        self._user_input_failure.clear()
        return self.user_input_failures[-1]

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        self.deltas.append(text_delta)

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        self.thinking_pulses += 1

    async def plan_updated(self, turn_token: TurnToken, entries: Any) -> None:
        self.plans.append([(entry.text, str(entry.status)) for entry in entries])

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        self.token_usages.append((input_tokens, output_tokens, cached_input_tokens, cost_usd))

    async def context_compacted(self, turn_token: TurnToken) -> None:
        self.compactions += 1

    @property
    def agent_message_texts(self) -> list[str]:
        """The words of each finished message. The messages themselves are above."""
        return [message_content_text(content) for content in self.agent_contents]

    async def agent_message_completed(self, turn_token: TurnToken, content: MessageContent) -> None:
        self.agent_contents.append(content)

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        self.tool_calls_started.append((tool_call_id, title, tool_kind))

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        self.tool_calls_progressed.append((tool_call_id, detail))

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        self.tool_calls_finished.append((tool_call_id, tool_call_status))

    async def permission_ask_raised(self, turn_token: TurnToken, ask: BackendPermissionAsk) -> None:
        self.asks.append(ask)
        self._an_ask.set()

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        self.user_inputs.append(request)
        self._user_input.set()

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        self.user_input_failures.append((request_id, detail))
        self._user_input_failure.set()

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        self.endings.append(ending)
        self.error_summaries.append(error_summary)
        self.standard_error_tails.append(standard_error_tail)
        self._turn_over.set()

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        self.vendor_session_cursor = vendor_session_cursor

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        self.composer_catalog_reports.append(composer_catalog)


@dataclass
class _ScriptedChild:
    """One adapter, one scripted app-server, and the child's own account of what it got."""

    child: CodexAppServerBackendChild
    sink: _RecordingSink
    transcript_path: Path
    workspace: Path
    # The same store the adapter was built with, so a test can keep a file and know the
    # adapter is looking in the place it was kept.
    message_files: ConversationMessageFiles

    async def start(self, *, cursor: str | None) -> None:
        await self.child.start(_resolved_start(self.workspace), vendor_session_cursor=cursor)

    async def write_prompt(
        self,
        turn_number: int,
        content: MessageContent,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        await self.child.write_prompt(
            TurnToken(conversation_id="c", turn_number=turn_number),
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change=model,
            reasoning_effort_change=reasoning_effort,
        )

    def transcript(self) -> list[dict[str, Any]]:
        if not self.transcript_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.transcript_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def all_sent(self, method: str) -> list[dict[str, Any]]:
        return [
            entry["received"]
            for entry in self.transcript()
            if "received" in entry and entry["received"].get("method") == method
        ]

    def sent(self, method: str, *, missing_is_none: bool = False) -> Any:
        sent = self.all_sent(method)
        if not sent and missing_is_none:
            return None
        assert sent, f"{method} never reached the child"
        return sent[0]

    async def wait_for_an_answer(self, *, seconds: float = 5.0) -> None:
        """Wait until an answer this adapter sent has reached the child and been written down."""
        waited = 0.0
        while not self.answers() and waited < seconds:
            await asyncio.sleep(0.05)
            waited += 0.05
        assert self.answers(), "nothing this adapter answered ever reached the child"

    def what_happened_at_the_child(self) -> list[str]:
        """The child's own order of events: what it was sent, and what it sent back.

        Ordering is the whole assertion for a race, and the only account of it that cannot
        be fooled by this side's bookkeeping is the child's.
        """
        happened: list[str] = []
        for entry in self.transcript():
            if "received" in entry and entry["received"].get("method"):
                happened.append(f"received {entry['received']['method']}")
            elif "emitted" in entry:
                happened.append(f"emitted {next(iter(entry['emitted']))}")
        return [
            step
            for step in happened
            if step.startswith("emitted") or step.split()[1] in {"turn/start", "turn/interrupt"}
        ]

    def launched(self) -> dict[str, Any]:
        launched: list[dict[str, Any]] = [
            entry["launched"] for entry in self.transcript() if "launched" in entry
        ]
        assert launched, "the child never said how it was launched"
        return launched[0]

    def answers(self) -> list[dict[str, Any]]:
        return [entry["answer"] for entry in self.transcript() if "answer" in entry]


@asynccontextmanager
async def _scripted_child(
    workspace: Path, *, script: dict[str, Any]
) -> AsyncIterator[_ScriptedChild]:
    transcript_path = workspace / "transcript.jsonl"
    script_path = workspace / "script.json"
    script_path.write_text(
        json.dumps({"transcript_path": str(transcript_path), **script}), encoding="utf-8"
    )
    argv, environment = scripted_app_server_launch(script_path)
    sink = _RecordingSink()
    message_files = ConversationMessageFiles(str(workspace / "planner.db"))
    child = CodexAppServerBackendChild(
        launch=CodexChildLaunch(argv=argv, environment_overrides=tuple(environment.items())),
        resolved_start=_resolved_start(workspace),
        event_sink=sink,
        message_files=message_files,
    )
    try:
        yield _ScriptedChild(
            child=child,
            sink=sink,
            transcript_path=transcript_path,
            workspace=workspace,
            message_files=message_files,
        )
    finally:
        await child.stop()


def test_a_picture_reaches_codex_as_the_file_it_is(tmp_path: Path) -> None:
    """Codex takes a picture as a path, and a path is exactly what this system has.

    Nothing is encoded and nothing is copied: the bytes are already on disk beside the
    record, and the piece names them.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            kept = await scripted.message_files.keep(
                "c", b"\x89PNG not really", media_type="image/png"
            )
            await scripted.child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                (
                    MessageText(text="look at this"),
                    MessageImage(stored_file_id=kept.stored_file_id, media_type="image/png"),
                ),
                sender_content=(
                    MessageText(text="look at this"),
                    MessageImage(stored_file_id=kept.stored_file_id, media_type="image/png"),
                ),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )

            given = scripted.sent("turn/start")["params"]["input"]
            assert given == [
                {"type": "text", "text": "look at this"},
                {"type": "localImage", "path": str(kept.absolute_path)},
            ]

    _run(exercise)


def test_a_file_reaches_codex_as_explicit_managed_path_context(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)
            kept = await scripted.message_files.keep("c", b"answer,42\n", media_type="text/csv")
            content = (
                MessageFile(
                    stored_file_id=kept.stored_file_id,
                    media_type="text/csv",
                    file_name="facts.csv",
                    byte_count=10,
                ),
            )
            await scripted.child.write_prompt(
                TurnToken(conversation_id="c", turn_number=1),
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change=None,
            )
            assert scripted.sent("turn/start")["params"]["input"] == [
                {
                    "type": "text",
                    "text": (
                        'Attached file "facts.csv" (text/csv, 10 bytes) is available at '
                        f"{kept.absolute_path}."
                    ),
                }
            ]

    _run(exercise)
