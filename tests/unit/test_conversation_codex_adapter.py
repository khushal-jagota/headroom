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
    CodexChildLaunch,
)
from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    BackendSpawnFailed,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    AgentCommand,
    ConversationAccess,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding, ToolCallStatus
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessageText,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles


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


def test_codex_offers_no_commands_because_its_protocol_has_none_to_declare(
    tmp_path: Path,
) -> None:
    """Not a gap in this adapter — codex's wire has no notion of a command a person types.

    Codex's slash commands live inside its own terminal program and are dispatched there,
    so nothing declares them over the protocol Panels speaks. A codex conversation has
    none to offer, and this adapter says nothing at all rather than reporting an empty
    list, which would be codex claiming it had looked and found none.
    """

    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as scripted:
            await scripted.start(cursor=None)

            assert scripted.sink.available_commands_reports == []

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
            # Nothing was minted, so nothing is rebound: the cursor the core holds still
            # names the thread this conversation lives in.
            assert scripted.sink.vendor_session_cursor is None

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
    assert not any(
        "mcpServer/startupStatus/updated" in record.getMessage() for record in warnings
    )
    assert any(
        "mcpServer/startupStatus/updated" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.DEBUG
    )


def test_the_childs_standard_error_is_kept_bounded(tmp_path: Path) -> None:
    """A stderr burst can neither fill the pipe nor grow without limit."""

    async def exercise() -> None:
        flood = ("a codex log line that says very little\n" * 20_000)
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
        self.endings: list[ConversationTurnEnding] = []
        self.error_summaries: list[str | None] = []
        self.standard_error_tails: list[str | None] = []
        self.vendor_session_cursor: str | None = None
        self.available_commands_reports: list[tuple[AgentCommand, ...]] = []
        self._turn_over = asyncio.Event()
        self._an_ask = asyncio.Event()

    def expect_another_turn(self) -> None:
        self._turn_over.clear()

    async def wait_for_the_turn_to_end(self) -> None:
        await self._turn_over.wait()

    async def wait_for_an_ask(self) -> BackendPermissionAsk:
        await self._an_ask.wait()
        self._an_ask.clear()
        return self.asks[-1]

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

    async def agent_message_completed(
        self, turn_token: TurnToken, content: MessageContent
    ) -> None:
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

    async def permission_ask_raised(
        self, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        self.asks.append(ask)
        self._an_ask.set()

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

    async def available_commands_reported(
        self, available_commands: tuple[AgentCommand, ...]
    ) -> None:
        self.available_commands_reports.append(available_commands)


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
        text: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        await self.child.write_prompt(
            TurnToken(conversation_id="c", turn_number=turn_number),
            text,
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
