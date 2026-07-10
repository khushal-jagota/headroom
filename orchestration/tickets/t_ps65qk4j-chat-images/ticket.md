# Ticket t_ps65qk4j — Allow attaching images in chat

## Accepted contract

**Success:** A user can attach an image to a Panels chat message, send it to the actual chat/worker session, and see it inline in the transcript immediately and after reload. The image is persisted and served through Panels-managed files and rendered through the existing shared preview path. Scope is image-only.

**Approach:** Store chat images under one entity-scoped managed tree for ticket, day, and Chief chat. Persist a normal managed-file Markdown reference in Panels chat, extend the centralized preview contract for that target, and call Hermes `image.attach` with the saved path immediately before `prompt.submit` so the real session receives the image.

## Owner correction

The image affordance sits directly beside the existing `/` button in the shared chat composer. Preserve the current composer layout and every existing text, command, draft, pause, send, and scrolling interaction; do not redesign the chat box.

## Constraints

- Strict TDD: capture focused RED before each production slice, then GREEN.
- Canonical chat text remains database text; no attachment rows, global registry, or general file product.
- Validate bounded image uploads by real content, not filename/MIME claim alone.
- Managed path resolution must reject traversal, encoded traversal, directories, missing files, and symlink escapes.
- Ticket, day, and Chief use the same chat-owned storage convention.
- Delivery must be proved through Hermes RPC ordering (`image.attach` before `prompt.submit`), not inferred from a `chat_messages` row.
- Reuse `FilePreviewTarget` / `resolvePreview` / `FilePreview`; no chat-local image renderer.
- The worktree already contains unrelated, uncommitted chat auto-scroll and gateway interrupt changes in `ChatPanel.svelte`, `assets/app.css`, `shared_gateway.py`, and tests/docs. Preserve those hunks exactly and add only ticket-owned edits around them.
- Final proof is one clean `./verify` after all changes and reviews land.
