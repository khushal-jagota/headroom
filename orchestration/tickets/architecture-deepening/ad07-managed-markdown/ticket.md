# AD07 — Deep Managed Markdown

## Outcome

Make Managed Markdown one deep frontend module. A caller supplies one host element, one Markdown source,
and read-only or editable intent. The module owns hardened rendering, empty-state presentation, conversion
of rendered anchors into `FilePreview` components, editable atomic-preview behavior, exact Markdown
serialization, preview reconciliation, and teardown.

`MarkdownBlock` and `InlineEdit` remain the product components, but they stop coordinating Markdown DOM,
preview cleanup callbacks, source-token serialization, or mutation-observer lifetime themselves. They ask
the Managed Markdown owner to mount/update/read/destroy one surface. `FilePreviewTarget`, `FilePreview`, and
the hardened renderer retain the responsibilities they already earn.

This is an internal concentration ticket. It does not redesign written text, editing, file cards, nested
previews, or any route. Exact visible and serialized behavior is a preservation contract.

## Why this exists

The current Markdown boundary is shallow and split:

- `MarkdownBlock.svelte` invokes the global renderer, adds the shared class, mounts previews, stores a
  cleanup callback, hand-compares render inputs, clears DOM, and tears previews down;
- `markdownEdit.ts` repeats rendering and preview mounting for editable Markdown, separately owns exact
  DOM-to-Markdown serialization, and returns a cleanup callback to its caller;
- `InlineEdit.svelte` owns that callback, decides when to repaint/read/refresh, and coordinates component
  teardown around its ordinary edit state;
- `filePreviewMount.ts` owns anchor replacement, Svelte mounting, atomic source tokens, caret guards,
  mutation observation, and unmounting, but callers still need to sequence it correctly; and
- this split makes preview identity and cleanup depend on each caller remembering the same lifecycle.

These are one concern: a Managed Markdown surface. The module should expose less and own more. It must not
grow a generic rich-text framework, Markdown AST, document store, editor toolbar, or file registry.

## Contract files

Implement against declarations frozen after plan review in:

- `web/src/lib/managedMarkdown.ts` — the one Managed Markdown lifecycle owner and its closed options;
- `web/src/lib/filePreview.ts` — the existing `FilePreviewTarget` / resolution and recursive-bound contract,
  preserved rather than absorbed;
- `web/src/components/MarkdownBlock.svelte` — the read-only product wrapper; and
- `web/src/components/InlineEdit.svelte` — the ordinary edit-state wrapper for Markdown and plain text.

The reviewed plan must name the owner, constructor/factory, public methods, options, and result types
exactly. It must decide whether the old `markdownEdit.ts` and `filePreviewMount.ts` disappear or are reduced
to honest non-lifecycle roles. A compatibility wrapper that preserves their current public lifecycle
functions is not acceptable.

## Required behavior

### One owner for one surface

There is one frontend object or closure-backed value responsible for a Managed Markdown host. It receives
read-only or editable intent once, owns every mounted preview beneath that host, and provides only the
operations the two product wrappers require:

- render or idempotently update a source and its read-only presentation inputs;
- serialize the current editable DOM to canonical Markdown when editable;
- refresh the editable empty-state marker without exposing serialization internals; and
- dispose the whole surface exactly once.

The delegated plan must freeze this interface before implementation. `MarkdownBlock` must not import the
hardened global renderer or preview-mounting helper directly. `InlineEdit` must not store a Markdown preview
cleanup callback or import DOM-to-Markdown serialization functions. Neither wrapper may walk preview DOM,
mount/unmount a `FilePreview`, or own a `MutationObserver`.

Plain-text editing is not Managed Markdown. Existing plain focus, blur, save, keyboard, snapshot, and paste
behavior remains in `InlineEdit` or in an honestly named plain-edit helper; it must not be generalized into
the Markdown owner.

### Exact read-only presentation

Read-only Markdown continues to use `window.Planner.markdown.render`, the only user-derived `innerHTML`
boundary. Non-empty source receives the same `markdown markdown-block` DOM and CSS. Empty source renders the
same `quiet-line` and caller-provided text. A missing renderer falls back to safe text exactly as today.

Every rendered anchor still becomes the same `FilePreviewTarget` plus `FilePreview` component. Image,
video, audio, Managed Markdown, HTML, download, external-link, ticket-file, and chat-file behavior remain
unchanged. Managed Markdown recursion still uses the current fixed depth and visited-href bound. HTML keeps
the shared `allow-scripts` sandbox at an opaque origin. External targets are never fetched for metadata.

An idempotent update with identical source, empty text, depth, and visited values preserves the existing
rendered DOM and mounted `FilePreview` identity. An unrelated parent/resource update therefore cannot abort
an in-flight preview, clear an iframe, or replace a loaded media node. A genuinely changed render input
reconciles the surface and disposes only the previews it replaces.

### Exact editable presentation and serialization

Editable Markdown stays the current single continuous `contenteditable`. There is no source mode and no
Edit/Save/Cancel control set. Rendered links remain atomic preview islands inside the editor with the same
before/after caret guards. Each island carries the renderer-authored exact
`data-markdown-source-token`; serialization emits that token and ignores all generated descendants.

The current focus, blur/save, Cmd/Ctrl+Enter, single-line Enter, Escape, paste, error, retry, and no-op
semantics remain exact. Merely focusing or using a preview action writes nothing. An unchanged approval
draft still omits `edited_body`. Browser structure edits may remove and reinsert an atomic slot; if the slot
is still inside the same root when reconciliation runs, its component and node identity stay alive. A true
deletion removes the source token, unmounts the preview once, aborts pending fetches, and clears HTML iframe
content.

Serialization preserves the current supported Markdown exactly: paragraphs and line breaks, H1–H3,
unordered and ordered lists, fenced code, inline code, bold, italic, ordinary anchors, nonbreaking spaces,
zero-width caret guards, adjacent atomic tokens, and deletion of a selected atomic token. It never persists
`data-file-preview`, generated text/media, iframe markup, or Svelte DOM.

### File-preview boundary remains separate

The Managed Markdown owner classifies no extensions and fetches no files itself. It converts an anchor to
the existing `FilePreviewTarget` and mounts the existing `FilePreview`; that component and
`filePreview.ts` continue to own target safety, kind resolution, managed hrefs, recursive Markdown
eligibility, fetch/abort, media, iframe, and actions. `FilePreview` may recursively render a
`MarkdownBlock`, which creates a distinct child Managed Markdown surface carrying the existing depth and
visited inputs.

### Static ownership and deletion

After migration there is one production use of the hardened renderer and one production site that mounts
`FilePreview` for rendered Markdown anchors, both inside the Managed Markdown module. There is one
`MutationObserver` owner and one exact serializer. The old public functions
`paintMarkdownEditable`, `readMarkdownEditable`, `refreshEditableEmptyState`, and `mountFilePreviews` are
deleted rather than forwarded. If old files remain for plain-text helpers, their names and exports must say
plain text and contain no Markdown renderer, serializer, preview, Svelte mount, or observer logic.

## RED-first acceptance tests

Add or replace a focused frontend contract suite chosen in the reviewed plan and preserve the existing
Playwright suites. Prove:

1. **Exact owner/deletion boundary.** Static tests find one Managed Markdown lifecycle owner; only it calls
   `window.Planner.markdown.render`, mounts/unmounts `FilePreview`, creates a `MutationObserver`, handles
   Markdown source tokens, and serializes Markdown. The two wrappers have no cleanup callback or direct
   lifecycle implementation. Old lifecycle exports and compatibility aliases are absent.
2. **Read-only parity.** Empty/fallback and non-empty rendering retain exact classes/text; all seven preview
   kinds and ticket/chat managed targets are unchanged; nested and self/depth-bounded Markdown remain exact.
3. **Idempotent identity.** Updating with the same complete render input preserves host child, atomic slot,
   mounted preview/media/iframe identity and does not abort an in-flight fetch. Changing source disposes the
   replaced previews exactly once.
4. **Editable serialization.** The complete current serializer matrix round-trips exact raw Markdown and
   ignores generated preview descendants. Adjacent tokens, caret-guard edits, paste, ordinary links,
   formatting, and selected deletion remain covered.
5. **Editable lifecycle.** Structure-changing browser edits retain a reinserted slot; true removal aborts a
   pending Managed Markdown fetch and clears a loaded HTML iframe. Focus and preview actions are no-ops;
   save/reload restores previews from exact stored source; Escape and failed-save retry remain exact.
6. **Caller parity.** Ticket fields/notes/recaps, gating and result proposals, Review drafts, Day fields,
   Sprint/Idea read-only text, and every persisted Chat role continue through the same Managed Markdown
   owner. A Chat/resource update unrelated to a message's Markdown preserves an existing preview node.
7. **Safety.** The hardened renderer remains the sole innerHTML boundary for user Markdown; executable URL
   normalization, HTML escaping, exact source-token escaping, managed-path rejection, sandbox, and external
   no-fetch rules stay green.

Do not weaken a preservation assertion to fit the refactor. Browser behavior remains asserted by the
existing Playwright tests, including `test_ticket_file_previews.py`, `test_flows_a.py`, `test_flows_b.py`,
and Chat-image/live-Chat suites.

## Documentation

Update `docs/frontend.md` and synchronized `docs/systems.md` / `docs/systems.html` to describe the one
Managed Markdown owner in plain language: wrappers own product interaction, the module owns Markdown DOM
and preview lifetime, and `FilePreview` owns target-specific behavior. Remove implementation wording that
still assigns lifecycle ownership to the wrappers or old helpers. Rebuild checked-in `web/dist` because the
local server serves it.

## Explicit exclusions

- No backend, HTTP, database, event, resource-cache, or file-serving change.
- No visible, CSS, token, accessibility, editor-mode, keyboard, or interaction redesign.
- No new Markdown syntax, parser library, sanitizer, AST, rich-text editor, toolbar, or source mode.
- No preview-kind, recursive-bound, fetch, sandbox, media, download, or external-link behavior change.
- No upload/artifact/file-id/attachment model.
- No AD08 resource catalogue or event invalidation work.
- No AD09 Panels Chat / Employee history separation.

## Plan requirements

The delegated plan must inventory every production/test caller of the renderer, Markdown lifecycle helpers,
preview mount/unmount, serializer, `MarkdownBlock`, and editable Markdown; every preview kind and recursive
edge; every current no-op/save/retry/deletion/identity browser test; and every checked-in built asset that
changes. It must define exact declarations, state transitions, idempotence keys, teardown rules, RED order,
preservation commands, and a bounded changed-path allowlist. Any visible behavior, parser, file-preview
contract, backend, or resource-cache change returns to the orchestrator before implementation.
