from __future__ import annotations

from acp.schema import (
    AgentMessageChunk,
    FileEditToolCallContent,
    SessionNotification,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from planner.conversation.codex_session_notification_normalizer import (
    CODEX_FILE_EDIT_DETAIL_BYTE_LIMIT,
    PANELS_CODEX_FILE_EDIT_METADATA_KEY,
    _bounded_equal_prefix,
    normalize_codex_session_notification,
)


def _notification(*contents: FileEditToolCallContent) -> SessionNotification:
    return SessionNotification(
        session_id="session-codex",
        update=ToolCallStart(
            session_update="tool_call",
            tool_call_id="edit-1",
            title="Editing files",
            kind="edit",
            status="completed",
            content=list(contents),
            raw_input={"patch": "preserved"},
            raw_output={"result": "preserved"},
            field_meta={"source": "preserved"},
        ),
        field_meta={"notification": "preserved"},
    )


def _metadata(content: FileEditToolCallContent) -> dict[str, object]:
    assert content.field_meta is not None
    value = content.field_meta[PANELS_CODEX_FILE_EDIT_METADATA_KEY]
    assert isinstance(value, dict)
    return value


def test_non_edit_notification_is_returned_by_identity() -> None:
    notification = SessionNotification(
        session_id="session-codex",
        update=AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="unchanged"),
        ),
    )

    assert normalize_codex_session_notification(notification) is notification


def test_small_update_becomes_grouped_hunks_and_preserves_unrelated_fields() -> None:
    old = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n"
    new = "one\ntwo changed\nthree\nfour\nfive\nsix changed\nseven\neight\n"
    notification = _notification(
        FileEditToolCallContent(
            type="diff",
            path="notes.txt",
            old_text=old,
            new_text=new,
            field_meta={"adapter": "preserved"},
        )
    )

    normalized = normalize_codex_session_notification(notification)

    assert normalized is not notification
    assert normalized.session_id == notification.session_id
    assert normalized.field_meta == notification.field_meta
    assert isinstance(normalized.update, ToolCallStart)
    assert normalized.update.tool_call_id == "edit-1"
    assert normalized.update.title == "Editing files"
    assert normalized.update.kind == "edit"
    assert normalized.update.status == "completed"
    assert normalized.update.raw_input == {"patch": "preserved"}
    assert normalized.update.raw_output == {"result": "preserved"}
    assert normalized.update.field_meta == {"source": "preserved"}
    assert normalized.update.content is not None
    diffs = [
        item for item in normalized.update.content if isinstance(item, FileEditToolCallContent)
    ]
    assert diffs
    assert all(item.path == "notes.txt" for item in diffs)
    assert all(item.old_text != old and item.new_text != new for item in diffs)
    assert diffs[0].field_meta is not None
    assert diffs[0].field_meta["adapter"] == "preserved"
    assert _metadata(diffs[0])["operation"] == "update"
    assert _metadata(diffs[0])["detailState"] == "complete"
    assert _metadata(diffs[0])["oldStartLine"] == 1
    assert _metadata(diffs[0])["newStartLine"] == 1


def test_add_delete_and_large_update_have_bounded_truthful_metadata() -> None:
    huge = "".join(f"line {index:06d} {'x' * 80}\n" for index in range(20_000))
    notification = _notification(
        FileEditToolCallContent(type="diff", path="added.txt", old_text=None, new_text=huge),
        FileEditToolCallContent(type="diff", path="deleted.txt", old_text=huge, new_text=""),
        FileEditToolCallContent(
            type="diff", path="updated.txt", old_text=huge, new_text=huge + "tail\n"
        ),
    )

    normalized = normalize_codex_session_notification(notification)

    assert isinstance(normalized.update, ToolCallStart)
    assert normalized.update.content is not None
    diffs = [
        item for item in normalized.update.content if isinstance(item, FileEditToolCallContent)
    ]
    assert {item.path for item in diffs} == {"added.txt", "deleted.txt", "updated.txt"}
    assert sum(len(item.model_dump_json().encode()) for item in diffs) < 80_000
    metadata = {item.path: _metadata(item) for item in diffs}
    assert metadata["added.txt"]["operation"] == "add"
    assert metadata["deleted.txt"]["operation"] == "delete"
    assert metadata["updated.txt"]["operation"] == "update"
    assert all(value["detailState"] in {"truncated", "omitted"} for value in metadata.values())
    assert all(value["oldLineCount"] is None for value in metadata.values())
    assert all(value["newLineCount"] is None for value in metadata.values())
    mandatory = normalize_codex_session_notification(
        _notification(
            FileEditToolCallContent(type="diff", path="added.txt", old_text=None, new_text=""),
            FileEditToolCallContent(type="diff", path="deleted.txt", old_text="", new_text=""),
            FileEditToolCallContent(type="diff", path="updated.txt", old_text="", new_text=""),
        )
    )
    assert (
        len(normalized.model_dump_json().encode())
        <= len(mandatory.model_dump_json().encode()) + CODEX_FILE_EDIT_DETAIL_BYTE_LIMIT + 4096
    )


def test_progress_diff_is_normalized_and_metadata_key_collision_is_preserved() -> None:
    foreign = {
        "operation": "update",
        "detailState": "complete",
        "oldStartLine": 999,
        "newStartLine": 999,
        "oldLineCount": 1,
        "newLineCount": 1,
    }
    unsafe_ordinal = 9_007_199_254_740_992
    collision = {
        PANELS_CODEX_FILE_EDIT_METADATA_KEY: foreign,
        f"{PANELS_CODEX_FILE_EDIT_METADATA_KEY}#{unsafe_ordinal}": foreign,
    }
    notification = SessionNotification(
        session_id="session-codex",
        update=ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="edit-1",
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path="collision.txt",
                    old_text="before\n",
                    new_text="after\n",
                    field_meta=collision,
                )
            ],
        ),
    )

    normalized = normalize_codex_session_notification(notification)

    assert isinstance(normalized.update, ToolCallProgress)
    assert normalized.update.content is not None
    diff = normalized.update.content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.field_meta is not None
    assert diff.field_meta[PANELS_CODEX_FILE_EDIT_METADATA_KEY] == foreign
    owned = [
        key
        for key, value in diff.field_meta.items()
        if key.startswith(PANELS_CODEX_FILE_EDIT_METADATA_KEY)
        and isinstance(value, dict)
        and "detailState" in value
    ]
    assert owned == [
        PANELS_CODEX_FILE_EDIT_METADATA_KEY,
        f"{PANELS_CODEX_FILE_EDIT_METADATA_KEY}#{unsafe_ordinal}",
        f"{PANELS_CODEX_FILE_EDIT_METADATA_KEY}#{unsafe_ordinal + 1}",
    ]
    appended = diff.field_meta[
        f"{PANELS_CODEX_FILE_EDIT_METADATA_KEY}#{unsafe_ordinal + 1}"
    ]
    assert isinstance(appended, dict)
    assert appended["oldStartLine"] == 1


def test_large_middle_comparison_has_a_fixed_work_ceiling() -> None:
    shared = "a" * 1_000_000

    equal_within_bound, work = _bounded_equal_prefix(
        shared + "old middle" + shared,
        shared + "new middle" + shared,
    )

    assert equal_within_bound is True
    assert work == 16_384

    normalized = normalize_codex_session_notification(
        _notification(
            FileEditToolCallContent(
                type="diff",
                path="huge-middle.txt",
                old_text=shared + "old middle" + shared,
                new_text=shared + "new middle" + shared,
            )
        )
    )
    assert isinstance(normalized.update, ToolCallStart)
    assert normalized.update.content is not None
    diff = normalized.update.content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.old_text == ""
    assert diff.new_text == ""
    assert _metadata(diff)["detailState"] == "omitted"
