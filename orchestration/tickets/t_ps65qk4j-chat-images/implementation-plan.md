# Implementation dispatch

Work as five vertical RED→GREEN slices. Do not write production code for a slice until its focused test fails for the missing behavior.

1. **Managed chat-image boundary.** Extend `planner.files` with a chat-file contract and path resolver rooted at `<db-dir>/files/chats/<entity_id>/`. Reuse the existing containment and response policy. Add a human-only image upload route tied to a real chattable entity. Stream into a non-served temporary file, enforce the size bound while reading, sniff and validate the completed bytes, derive the canonical extension from the sniffed type rather than the client filename, then atomically rename into a server-generated collision-safe path; clean the temporary file on every failure or disconnect. Return the canonical `/files/chats/...` reference and keep the resolved absolute path inside backend code. Unit/API tests cover ticket, day, Chief, valid PNG/JPEG/GIF/WebP regardless of client extension, spoofed/unsupported content, oversize and interrupted input with no published remainder, traversal/encoding, missing/directory paths, and symlink escape.

2. **Centralized preview.** Add a `chat-file` branch to `FilePreviewTarget` and parse/build canonical serve and preview URLs without duplicating image classification. Extend the preview route to accept `source=chat&entity=<entity_id>&path=<path>`. Frontend tests and browser coverage prove the same `FilePreview` component renders a chat image inline and does not change ticket/external behavior.

3. **Real Hermes delivery.** Introduce the smallest attachment shape in the chat and gateway contracts: visible text/reference remains separate from a server-resolved local image path. At turn start, validate that the attachment reference is a managed chat file owned by the route's same `entity_id`; reject cross-entity, ticket-file, external, missing, and unsafe references before starting a turn or resolving a local path. `start_human_turn` stores the visible Markdown reference in `chat_messages`; `SharedGateway` sends `image.attach {session_id: live_sid, path: absolute_path}` after opening the session event stream and immediately before `prompt.submit`. Ensure attach failure settles the turn as failed and does not submit text. Unit tests prove same-entity acceptance, cross-entity rejection with no turn row, exact RPC order, resumed/live session identity, path payload, Panels transcript text, and no prompt submission after attach failure.

4. **Existing composer affordance.** Add one visually small image button directly beside `/`, backed by a hidden image file input and pending-image state. Do not move or replace `/`, the textarea, or the existing send/pause button. Preserve slash commands and draft/pause behavior. Allow text+image and image-only sends; retain the pending image on failed upload/start and clear it only after success. Use existing `ErrorLine`; do not add an attachment manager, modal, toolbar, or editor controls.

5. **Behavioral closeout.** Extend live-chat e2e for both ticket and Chief: select a real image, send, verify the human message contains the shared inline preview with non-zero geometry, navigate/reload/remount, and verify the same managed URL remains. Exercise invalid-file rejection and the absence of duplicate submission. Update `docs/chat.md` (system contract) and only the minimal frontend sentence needed. Rebuild `web/dist` from the settled source.

## Shared-worktree integration guard

Before editing and again before handoff, capture and compare the exact pre-existing diffs in `ChatPanel.svelte`, `assets/app.css`, `shared_gateway.py`, their tests, and chat/frontend docs. Preserve the unrelated auto-scroll, live-session interrupt, Workspace URL, and delete-control hunks byte-for-byte. If any of those files change concurrently during implementation, stop, re-read the merged file, and rerun the ticket's focused checks against the settled combined state before rebuilding `web/dist`.

Focused verification before review:

- `.venv/bin/python -m pytest tests/unit/test_ticket_files.py tests/unit/test_chat_seed.py tests/unit/test_minds.py -q` plus any new focused unit file.
- `npm --prefix web run check && npm --prefix web run build`.
- `.venv/bin/python -m pytest tests/e2e/test_live_chat_state.py tests/e2e/test_ticket_file_previews.py -q`.

Then request read-only Codex implementation review against this dispatch and `ticket.md`, fix or disposition every finding, and run one final `./verify`.
