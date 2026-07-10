# Follow-up plan — deterministic previews in every Markdown surface

## User contract

Every Markdown link is handled by the shared `FilePreview` subsystem. The agent only creates a file or URL and writes an ordinary Markdown link; code decides the presentation from the target.

- Safe images, video, and audio render inline.
- Managed Markdown documents render inline through the hardened Markdown renderer.
- Managed HTML remains a card, optionally with a sandboxed non-interactive preview; clicking opens the full sandboxed Panels preview route in a new tab.
- Unsupported managed files remain `FilePreview` download cards.
- External URLs remain `FilePreview` external-link preview cards. Use label, URL/domain, and a clear open action; do not add server-side unfurling or arbitrary external fetches.
- Notes, approved fields, proposals/results, chat, and future Markdown surfaces behave the same. No consumer branches on extension and no LLM chooses presentation.

## Implementation shape

1. **Prove the real regression first.** Extend Playwright coverage against normal editable ticket states—not the prior `dropped` shortcut—to show that a passed field, ticket/field note, recap, and current proposal/result render `FilePreview` variants at rest. Assert the present implementation fails because direct anchors remain and Markdown/HTML hit attachment responses.
2. **Make Markdown editing dual-mode.** Refactor the shared Markdown path in `InlineEdit` so its at-rest state uses `MarkdownBlock` and therefore the centralized preview component. An explicit, accessible edit action switches to a literal Markdown source editor: `[label](href)` must be visibly present and no preview DOM may be inside the editor. Save/cancel returns to rendered mode and preserves the exact stored source. Keep non-Markdown inline editing unchanged. Apply the same at-rest/rendered versus active-source-edit behavior to the local pending-proposal draft in `ApprovalBlock`; test its distinct edit, cancel, and approve-payload path.
3. **Complete deterministic `FilePreview` variants.** Extend the resolver contract with the display metadata and action semantics the cards need; do not parse card behavior ad hoc in consumer surfaces. Render managed Markdown inline in embedded mode. Render HTML as a descriptive card whose preview iframe receives text only through `srcdoc`, has `sandbox=""` with no allowances, and whose `target="_blank" rel="noopener noreferrer"` action opens the Panels full-preview hash route. Render unsupported ticket files as download cards. Render external URLs as the shared external-link card (label + deterministic display URL/hostname + open action). Every rendered Markdown anchor is replaced by `FilePreview`; no generic anchor fallback remains outside the component.
4. **Bound nested Markdown deterministically.** Managed Markdown may contain more file links. Route those nested anchors through `FilePreview` too, while carrying depth and visited managed-file hrefs. Expand a managed Markdown document only when it is not already visited and the fixed depth limit has not been reached; otherwise the same `FilePreview` renders a compact open-preview card. Add nested and self-link tests. External targets are never fetched for metadata or inline content.
5. **Prove source preservation and navigation.** Rewrite—not preserve—the old assertions that embedded Markdown/HTML are links and editable-at-rest fields have no previews. In Playwright, inspect normal passed fields, ticket/field notes, recap, and current proposal/result; visible media geometry; inline Markdown content; HTML iframe sandbox/parent isolation; card link targets; and download/external actions. Enter edit mode with the explicit action, assert literal Markdown source and zero preview nodes, perform a real keyboard mutation, save, reload, verify previews return, and verify the canonical Markdown links remain unchanged in SQLite with no generated preview HTML. For approval drafts, also prove Escape restores the proposal and approval sends the edited Markdown.
6. **Close out.** Update frontend/docs wording and the managed-preview engineering reference to describe at-rest rendering/edit-mode separation. Run focused frontend checks and browser tests, live-check the completed behavior on the existing ticket proof links, obtain Codex diff review with no unresolved findings, then run one clean `./verify`.

## Boundaries

No upload API/UI, artifact rows or IDs, global registry, per-stage attachment slots, external metadata crawler, or LLM preview decision.
