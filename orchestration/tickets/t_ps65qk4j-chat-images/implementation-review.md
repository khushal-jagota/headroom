# Codex implementation review

Review command used `codex exec --model gpt-5.5 --sandbox read-only` with high reasoning against the accepted ticket, reviewed dispatch, checklist, AGENTS.md, PRINCIPLES.md, the entire uncommitted diff, and new files.

## Findings

1. **High — malformed PNG-like bytes can pass validation.** `src/planner/files/chat_images.py` only checks signature/IHDR dimensions and a fixed IEND suffix; it does not establish a structurally valid image.
2. **High — publication can write through an entity-root symlink before containment validation.** `os.replace` occurs before `resolve_chat_file`, so a symlinked `files/chats/<entity_id>` can receive the file outside the managed tree before rejection.
3. **Medium — a pending image changes slash commands into API errors.** `ChatComposer` forwards the pending image for command submissions, while the backend correctly rejects images in command mode.

All findings are accepted. They require tests and fixes before final verification. A clean follow-up Codex review is required after the fixes.

## Resolution

- Image validation now parses PNG chunks and CRCs, JPEG segments, GIF blocks, and WebP containers;
  valid static and animated WebP fixtures remain accepted while malformed synthetic payloads fail.
- Publication rejects symlinked files/chat/entity roots before using a no-follow entity directory
  descriptor for the atomic rename. Reads also reject entity-directory symlinks inside the chat root.
- Commands never carry the pending image; a browser regression proves `/status` remains a command and
  the selected image remains pending.
- Follow-up review caught and closed chat-root symlinks, same-root entity symlinks, malformed non-PNG
  payloads, and animated WebP compatibility. The final high-effort review ended `NO VIOLATIONS`.
