# Follow-up implementation report

## Changed

- Every at-rest Markdown surface now runs through `MarkdownBlock` and the single `FilePreview` subsystem, including ticket/field notes, recaps, passed fields, approval drafts, result proposals, chat, Day/Sprint Markdown fields, and legacy proposal cards.
- Markdown editing is explicit source mode. The editor shows literal `[label](href)` text; save/cancel returns to rendered previews and generated preview DOM never enters persistence.
- Managed Markdown renders inline through the hardened renderer. Nested links remain `FilePreview` instances, with a fixed depth and visited-href guard that converts self/deep links to compact preview cards.
- HTML is a card with fetched `srcdoc` in `sandbox=""`; its action opens the full Panels preview route in a new tab. Unsupported managed files are download cards. External URLs are external-link cards with deterministic hostname text and no metadata fetch.
- The edit affordance reserves layout space instead of overlapping rendered content. The production frontend bundle was rebuilt.

## TDD and focused proof

- Resolver/card metadata and Markdown expansion tests failed before implementation and pass afterward: `node web/tests/file-preview.test.mjs`.
- A normal passed field test failed because the old editable surface had direct anchors; after the dual-mode editor change it renders previews at rest.
- A result-proposal test then failed because `ProposalCard` still exposed a permanent raw textarea; it now uses the same rendered-at-rest/source-edit component and passes.
- `.venv/bin/python -m pytest tests/e2e/test_ticket_file_previews.py tests/e2e/test_flows_a.py tests/e2e/test_flows_b.py -q` — 22 passed.
- `npm --prefix web run check` — 0 errors; only the three existing `TicketRoute.svelte` initial-id warnings.
- Browser coverage asserts normal ticket and field notes, recaps, passed fields, gating and non-gating proposals/results, chat roles, inline image/media/Markdown, nested/self Markdown bounds, embedded/full HTML sandbox attributes and isolation, download/external cards, raw-source keyboard editing, Escape/cancel, approval payloads, persistence, reload, and preview restoration.

## Live proof

The real ticket `t_c5sb8b0h` was opened against the running Panels server with fresh built assets. Its approved result showed the screenshot inline, the managed Markdown document rendered inline, the HTML card rendered its sandboxed preview and linked to the Panels full-preview route in a new tab, and the unsupported `.bin` file appeared as a download card. The source-edit action was present at rest. Rendered image geometry was 1440×900 intrinsic and 607.7×379.8 CSS pixels, preserving aspect ratio. Playwright read the embedded HTML heading/body from the sandboxed frame.

## Review

Codex `gpt-5.5`, read-only sandbox, high reasoning, found gaps in the legacy proposal card and acceptance coverage plus an edit-button overlap risk. All findings were fixed or proven through stronger browser assertions. The focused follow-up review returned `NO VIOLATIONS`.

## Final verification

`./verify` completed with `VERIFY: PASS`.
