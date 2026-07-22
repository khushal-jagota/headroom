# Contract: bounded Codex file-edit conversation payloads

## Problem

The pinned Codex ACP adapter expands an update patch into complete `oldText` and `newText` file
snapshots. A small append to large files can therefore consume the complete Panels replay budget.
When the reset buffer crosses its byte ceiling, Panels correctly rejects the incomplete replay, but
the user loses all visible history and cannot send another message.

## Required behavior

1. Panels owns the correction; no installed dependency or generated `node_modules` file is patched.
2. Every Codex session notification crosses one Codex-specific typed normalizer before it reaches
   either ordinary live ingress or private `session/load` replay capture.
3. A Codex file-edit tool call never forwards complete unchanged file snapshots. It preserves:
   - tool-call identity, title, kind, lifecycle status, and unrelated fields;
   - each changed file path and add/update/delete identity;
   - bounded changed hunks with enough context to understand the edit;
   - original old/new line origins and an explicit marker when detail was elided or truncated.
4. Edit normalization has deterministic per-file and per-notification bounds. A large replacement,
   add, or delete cannot recreate the same amplification through changed content alone.
5. All non-edit Codex notifications and every notification from other backends are unchanged.
6. Live and replay presentation use the same normalized payload. The affected durable session for
   Ticket `t_m024gke4` must produce a complete reset through `ready` below the existing 1 MiB ceiling.
7. The existing reset-buffer overflow behavior remains fail-closed for genuinely oversized replay;
   this ticket does not raise or remove the integrity limit.
8. The browser renders bounded edit evidence truthfully and does not invent complete-file line
   numbers. Existing ordinary diff presentation remains compatible.

## Named gates

- Focused backend unit tests for identity normalization, update/add/delete bounds, unchanged
  non-edit behavior, and both live and capture-load ingress.
- Focused frontend tests for bounded-hunk metadata and line-origin/truncation presentation.
- A Hub/replay regression whose pre-fix payload crosses 1 MiB and whose normalized replay reaches
  `ready` without weakening the byte ceiling.
- Read-only live reconstruction of `t_m024gke4` proves reset/history/ready after restart.
- One final canonical `./verify` on the settled tree.
