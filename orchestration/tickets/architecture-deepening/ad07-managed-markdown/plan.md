# AD07 implementation plan — Deep Managed Markdown

## 1. Fixed scope and outcome

This ticket is a lifecycle concentration, not a behavior change.

Add one `managedMarkdown.ts` owner for each rendered Markdown host. It owns the renderer call, rendered
Markdown DOM, anchor-to-`FilePreview` mounting, editable atomic islands, exact serialization, preview
reconciliation, empty state, and teardown. `MarkdownBlock.svelte` remains the read-only product wrapper.
`InlineEdit.svelte` remains the product interaction and save-state wrapper. Neither wrapper keeps Markdown
DOM or preview lifecycle state.

Keep these existing boundaries unchanged:

- `assets/markdown.js` is the hardened parser/renderer and the only user-derived `innerHTML` writer.
- `filePreview.ts` owns `FilePreviewTarget`, target safety, kind resolution, preview hrefs, and the fixed
  recursive Markdown depth/visited rules.
- `FilePreview.svelte` owns target-specific fetch/abort, media, HTML iframe, actions, and recursive child
  `MarkdownBlock` behavior.
- `FilePreviewRoute.svelte` remains the separate full-preview route owner. It is not an anchor-mounted
  Managed Markdown surface.
- `InlineEdit.svelte` still owns focus, blur, save, error, retry, keyboard, Escape, and plain-versus-Markdown
  product behavior.

There is no new syntax, parser, sanitizer, editor mode, toolbar, source mode, CSS, backend contract,
resource invalidation, or visible interaction.

## 2. Complete current ownership and caller inventory

### Direct lifecycle and renderer sites to replace

- `MarkdownBlock.svelte` directly calls `window.Planner.markdown.render`, compares the complete read-only
  render input, clears and appends DOM, calls `mountFilePreviews`, retains its cleanup callback, and invokes
  cleanup on replacement/destruction.
- `markdownEdit.ts` calls the renderer for editable Markdown, calls `mountFilePreviews`, returns its cleanup
  callback, and contains the only Markdown DOM serializer.
- `InlineEdit.svelte` retains the editable preview cleanup callback, calls the Markdown painter/reader/empty
  functions, tracks `markdownDirty`, and sequences repaint around save, retry, Escape, and component
  destruction.
- `filePreviewMount.ts` is the only rendered-anchor mount/unmount site. It creates atomic slots and caret
  guards, copies the renderer-authored exact source token, creates the editable `MutationObserver`, retains
  mounted component handles, and unmounts removed or destroyed previews.
- `assets/markdown.js` is the hardened renderer. It authors escaped `data-markdown-source-token` values on
  rendered anchors and contains the sole user-derived `innerHTML` assignment. That boundary stays put.

After migration, `managedMarkdown.ts` is the only direct production caller of the renderer and the only
rendered-anchor `FilePreview` mount/unmount, observer, atomic-island, and serializer owner.

### `MarkdownBlock` read-only callers

- `IdeasRoute.svelte`: idea body.
- `TicketRoute.svelte`: the read-only recap branch.
- `SprintRoute.svelte`: primary bet.
- `ApprovalBlock.svelte`: read-only proposal body.
- `TicketStageSection.svelte`: review recap, user note, empty/current/passed/dropped value presentations.
- `ChatPanel.svelte`: persisted human/you, worker, system, and planner/assistant message roles. Empty assistant
  rows remain omitted by the caller as today.
- `FilePreview.svelte`: recursively fetched ticket Markdown. This creates a distinct child Managed Markdown
  surface with the existing next depth and visited hrefs.

### `InlineEdit` callers

Markdown mode:

- `TicketRoute.svelte`: editable ticket recap.
- `DayRoute.svelte`: take, watch, and lands bodies.
- `SprintRoute.svelte`: each Sprint document field.
- `ApprovalBlock.svelte`: proposal/draft body and user note.
- `TicketStageSection.svelte`: passed/current proposal value and user note, including Review drafts routed
  through these components.

Plain mode, which must not enter Managed Markdown:

- `TicketRoute.svelte`: title.
- `SprintRoute.svelte`: name.
- `ReviewRoute.svelte`: title.
- `DayRoute.svelte`: focus.

### Preview and recursion inventory

Every rendered anchor continues through `targetFromHref` and the same `FilePreview` component. Preserve all
seven resolved kinds: `markdown`, `image`, `video`, `audio`, `html`, `download`, and `external`. Preserve all
three target shapes: ticket file, chat file, and external link.

- Only safe ticket-file Markdown expands recursively in an embedded card.
- Chat-file Markdown remains a non-expanded card.
- Ticket Markdown expansion retains maximum depth 2 and the visited-href self/cycle bound.
- A recursive expansion calls `MarkdownBlock`, which owns a new child surface. Parent and child preview
  registries and teardown are independent.
- HTML keeps `sandbox="allow-scripts"` at an opaque origin. External links are never fetched for metadata.
- The explicit full preview route continues to mount `FilePreview` directly for non-HTML targets and to own
  its existing full HTML blob lifecycle.

No route or caller above changes API merely to accommodate this refactor. The two wrapper components remain
the route-facing seams.

## 3. Freeze the Managed Markdown declarations

Create `web/src/lib/managedMarkdown.ts` with exactly these exported declarations:

```ts
export type ReadOnlyManagedMarkdownInput = Readonly<{
  source: unknown;
  emptyText: string;
  depth: number;
  visited: readonly string[];
}>;

export interface ReadOnlyManagedMarkdownSurface {
  readonly mode: "read-only";
  update(input: ReadOnlyManagedMarkdownInput): void;
  destroy(): void;
}

export interface EditableManagedMarkdownSurface {
  readonly mode: "editable";
  update(source: unknown): void;
  hasChanges(): boolean;
  read(): string;
  refreshEmptyState(): void;
  destroy(): void;
}

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: "read-only" }
): ReadOnlyManagedMarkdownSurface;

export function createManagedMarkdownSurface(
  host: HTMLElement,
  options: { mode: "editable" }
): EditableManagedMarkdownSurface;
```

The implementation may use private types and closures, but exports no preview registry, serializer,
renderer, repaint helper, cleanup callback, observer hook, source-token helper, or generic mutable options.
Mode is selected once at creation. Calling a method after `destroy()` is an implementation error and must
be a harmless no-op; it must not remount or mutate the detached host.

Normalize source only inside the owner: `null` and `undefined` become `""`; every other value becomes
`String(value)`. Copy `visited` on update. Do not expose or retain a caller-owned mutable array.

## 4. Freeze the plain-edit boundary

Delete `markdownEdit.ts`; do not leave aliases for its Markdown lifecycle exports. Add
`web/src/lib/editableText.ts` containing only these existing plain/common contenteditable functions:

```ts
export function paintPlainEditable(el: HTMLElement, value: unknown): void;
export function readPlainEditable(el: HTMLElement): string;
export function editableMarkupSnapshot(el: HTMLElement): string;
export function editableMarkupChanged(el: HTMLElement, snapshot: string): boolean;
export function refreshPlainEditableEmptyState(el: HTMLElement): void;
export function handlePlainTextPaste(event: ClipboardEvent): void;
```

Move the current implementations without changing their behavior. `editableText.ts` must contain no
Markdown renderer call, Markdown serializer, source-token logic, FilePreview import, Svelte mount/unmount,
or `MutationObserver`. Delete `filePreviewMount.ts`; do not retain `mountFilePreviews` as an alias.

The old public names `paintMarkdownEditable`, `readMarkdownEditable`, `refreshEditableEmptyState`, and
`mountFilePreviews` disappear from production and tests.

## 5. Managed Markdown state and lifecycle

### Private common state

Each surface owns:

- its one host and immutable mode;
- a `destroyed` flag;
- a registry keyed by atomic/read-only preview slot, with the Svelte component handle and an `unmounted`
  guard;
- at most one editable `MutationObserver`; and
- mode-specific last-render/dirty state below.

The renderer returns the existing detached `.markdown` element. The owner adds `.markdown-block`, replaces
eligible rendered anchors with preview slots, then appends that element to the host. When the renderer is
missing, it creates the current safe text fallback and never assigns user source to `innerHTML`.

`assets/markdown.js` remains the only code that writes user-derived `innerHTML`, and the only code that
authors an anchor's exact `data-markdown-source-token`. `managedMarkdown.ts` is the sole consumer that
copies that token onto an editable atomic slot and the sole serializer that reads it.

### Read-only updates and complete idempotence key

Read-only state stores the last normalized tuple:

```text
(source, emptyText, depth, visited[0], visited[1], ...)
```

Compare every value and visited order/value, not array identity. The first update always paints. A later
identical tuple is a complete no-op: no renderer call, DOM replacement, anchor scan, mount/unmount,
fetch abort, iframe clear, or identity change. This preserves loaded and in-flight preview subtrees when a
parent Svelte/resource update supplies equivalent values.

A changed tuple performs one full current-behavior repaint: unmount every preview owned by the old render
once, replace the host children, render empty/fallback/non-empty presentation, mount the new anchors, and
store the copied tuple. Empty source retains the exact `.quiet-line` and caller-provided text. Non-empty
rendered and fallback source retains `.markdown.markdown-block` and current text behavior.

### Editable updates and dirty same-source reset

Editable state stores an initially-unset `lastPaintedSource` plus `dirtySincePaint`. The owner attaches one
input listener to its host. Every input event sets `dirtySincePaint = true`; the wrapper no longer owns
`markdownDirty`. The unset sentinel makes the first `update`, including a first empty source, always paint.

`update(source)` follows this exact rule:

1. Normalize the source.
2. If the surface is pristine and the normalized source equals `lastPaintedSource`, do nothing. This is
   essential: a focus/blur with no input preserves noncanonical stored syntax and mounted preview identity.
3. Otherwise repaint from the normalized source, even when it equals `lastPaintedSource`; then set
   `lastPaintedSource` to that source and `dirtySincePaint = false`.

The third rule distinguishes a pristine same-input update from an editable reset after an input event. It
canonically restores generated DOM after a user edited and then serialized back to the prior source. Source
string equality alone is not a sufficient idempotence key for an editable surface.

`hasChanges()` returns `dirtySincePaint`. `read()` serializes the current DOM without repainting or clearing
the dirty flag, updates only the `data-empty` marker, and returns canonical Markdown.
`refreshEmptyState()` serializes only to compute the current empty marker; it also does not clear dirty
state or repaint. The owner may keep the serializer private and share it between those two methods.

Editable paint remains one continuous outer `contenteditable=true` surface. For every rendered anchor:

- convert its href and label through `targetFromHref`;
- create the same `.file-preview-slot`, `contenteditable=false`, and
  `data-markdown-atomic-slot="true"` island;
- copy the renderer-authored exact source token to `data-markdown-source-token`;
- add the same U+200B before/after caret guards; and
- mount `FilePreview` into the slot with current depth/visited values (editable top-level remains 0/empty).

There is no source view and no generated preview descendant can enter serialized output.

### Exact serializer preservation

Move the current serializer privately into `managedMarkdown.ts`; do not rewrite it as a new parser. Preserve
the exact current mapping for:

- text, paragraphs, `DIV` browser blocks, and `BR` line breaks;
- H1–H3;
- unordered and ordered lists with the current canonical markers;
- fenced `PRE`/`CODE`, inline code, bold, and italic;
- ordinary anchors;
- direct `.markdown-block` unwrapping;
- nonbreaking space to ordinary space conversion;
- removal of U+200B caret guards;
- adjacent atomic tokens and selected-token deletion; and
- atomic token emission from `data-markdown-source-token` while ignoring every generated descendant.

Unsupported/generated elements continue to serialize only their supported inline children. Do not persist
`data-file-preview`, media text, controls, iframe markup, loading/error copy, or Svelte DOM.

### Observer reconciliation and teardown

Read-only surfaces need no observer. An editable surface creates its one observer when its first preview is
mounted and reuses that observer for the surface lifetime. It observes `childList` changes through the host
subtree. It never creates an observer per preview or repaint.

After each mutation batch, reconcile removed registered slots against the final DOM. If `host.contains(slot)`
is still true, retain the exact slot and component handle: a browser remove/reinsert or structure move in
the same batch is not deletion. If it is false, remove the registry entry and unmount the component exactly
once. True deletion therefore aborts a pending Markdown fetch through `FilePreview` destruction and clears
a loaded HTML iframe through its existing `onDestroy` behavior.

Before a full repaint, disconnect the observer while disposing/replacing the owned tree, unmount all old
registry entries once, paint/mount the replacement, and reconnect the same observer if the replacement has
previews. Do not let observer records race explicit repaint teardown.

`destroy()` is idempotent. Its first call removes the input listener, disconnects the observer, unmounts
every still-registered component exactly once, clears private registries/references, and marks the surface
destroyed. This unmount must happen even though Svelte will remove the wrapper host. Later calls do nothing.
The owner does not destroy recursive child surfaces directly; unmounting the parent `FilePreview` causes
Svelte to destroy its child `MarkdownBlock`, which destroys its own surface.

## 6. Wrapper migrations and save/retry state

### `MarkdownBlock.svelte`

Create one read-only surface after `host` exists. Its reactive effect calls `update` with normalized props
only through the frozen input object. Delete the local renderer, complete-input comparison,
`cleanupPreviews`, DOM manipulation, and preview helper import. `onDestroy` calls `surface.destroy()` once.
Keep the wrapper props, `.markdown-host`, and `display: contents` styling unchanged.

### `InlineEdit.svelte`

Create one editable surface when `markdown` is true and the bound element exists. The component calls only
`update`, `hasChanges`, `read`, `refreshEmptyState`, and `destroy` for Markdown. Delete
`cleanupMarkdownPreviews` and `markdownDirty`. Plain mode imports the frozen functions from
`editableText.ts`.

Treat the `markdown` prop as the surface-mode selector. If it changes on an existing component, destroy the
old Markdown surface before painting the newly selected mode; if it changes back, create a fresh editable
surface. No current caller switches mode, but the wrapper must not retain a mode-mismatched owner.

Keep ordinary product state in the wrapper. Add one private `pendingMarkdownSave: string | null` because a
failed save is product retry state, not Markdown DOM state:

- On Markdown commit, if `surface.hasChanges()` is true, read the current source. Otherwise retry
  `pendingMarkdownSave` if present. If neither exists, exit edit mode without repaint or write.
- If source equals the current prop, clear the pending value, exit edit mode, and call `surface.update(raw)`.
  A dirty same-source update repaints canonically; a pristine no-input blur never reaches this call.
- Before a real save, retain the attempted raw source. On success, clear it, exit editing, and update the
  surface from the saved source.
- On failure, set it to the attempted raw source, remain editing, show the existing error, and update the
  surface from that source. Because the surface was dirty, this restores canonical rendered DOM and clears
  its dirty flag; a later blur still retries from `pendingMarkdownSave` without another input.
- A later input takes precedence: commit reads the newly dirty surface and replaces the pending source on
  the next attempt.
- Escape/cancel clears the pending value and updates from the cancel/current value before blur.

Keep the existing `editing`, `inFlight`, `error`, `reverting`, plain snapshot, focus containment, keyboard,
paste, and Svelte effect guards. The input handler calls `surface.refreshEmptyState()` in Markdown mode or
`refreshPlainEditableEmptyState(el)` in plain mode. The owner input listener, not the wrapper, records
Markdown dirtiness. Destruction calls `surface.destroy()`.

This preserves the important no-input case: raw `*`, `1)`, or `_italic_` source is never serialized merely
because the user focused and blurred it. It also preserves retry after a failed save.

## 7. RED-first tests and implementation order

Do not modify production until the focused contract suite is red for the missing owner/deletions.

### RED 1 — ownership, declarations, and deletion

Replace `web/tests/markdown-edit.test.mjs` with `web/tests/managed-markdown.test.mjs`, and replace its explicit
entry in `web/package.json`.

The new Node suite first asserts the static declaration and ownership shape; `npm run check` separately
proves the exact TypeScript overloads at compile time:

- the exact exported declarations and closed read-only/editable modes are present;
- only `managedMarkdown.ts` calls `window.Planner.markdown.render`, imports/mounts/unmounts the rendered
  `FilePreview`, constructs `MutationObserver`, and contains the serializer/atomic-slot consumer;
- `assets/markdown.js` remains the permitted source-token author and only user-derived `innerHTML` writer;
- `MarkdownBlock.svelte` and `InlineEdit.svelte` contain no preview cleanup callback, renderer call,
  mount/unmount, observer, serializer walk, or direct atomic token handling;
- `filePreviewMount.ts` and `markdownEdit.ts` are absent; and
- the four old lifecycle export names and compatibility aliases are absent.

Make the file scan bounded to production source directories and explicit approved exceptions so comments,
built bundles, and test fixtures do not make the ownership assertion brittle.

### RED 2 — owner behavior in isolation

Extend the current small fake-DOM/transpiled-module Node approach rather than introducing a browser library.
Stub the Svelte mount/unmount calls and `FilePreview`, then exercise only the public surface API. Cover:

- read-only empty, safe fallback, and rendered class/text parity;
- complete tuple idempotence preserving host child/slot identity and producing zero extra mount/unmount;
- changed source/empty text/depth/visited repainting and unmounting each replaced preview once;
- editable pristine same-source no-op;
- editable input followed by same-source update forcing canonical repaint and clearing `hasChanges()`;
- idempotent `destroy()` and no work after destroy;
- remove/reinsert retention versus true removal unmount-once behavior; and
- the full existing serializer matrix: blocks, line breaks, headings, lists, fenced/inline code, bold,
  italic, ordinary links, NBSP, U+200B, adjacent exact tokens, generated-descendant exclusion, and selected
  token deletion.

Keep `web/tests/markdown-renderer.test.mjs` unchanged for exact escaped source-token authorship and unsafe
renderer input. Keep `web/tests/file-preview.test.mjs` unchanged for all seven kinds, all target types,
managed-path safety, external handling, and recursive depth/visited bounds.

### RED 3 — browser lifecycle gaps

Add narrowly focused cases to `tests/e2e/test_ticket_file_previews.py` before production migration:

- mutate an editable surface, restore source-equivalent content, and assert an explicit owner update
  canonically repaints while a pristine same-source update keeps the preview node;
- make one Markdown save fail, blur again without further input, and prove the exact attempted source is
  retried and then reloads with previews intact.

Do not rewrite or weaken existing expectations. Existing cases already remain the browser proof for all
read-only/editable preview kinds, recursive/self bounds, exact source roundtrip, no source mode, focus/action
no-op, paste and adjacent deletion, abort/iframe cleanup, moved-slot identity, and proposal
`edited_body` omission.

### Production order after RED

1. Add `managedMarkdown.ts` to satisfy the owner unit contract without changing `FilePreview` or
   `filePreview.ts`.
2. Add `editableText.ts`; migrate `InlineEdit`; migrate `MarkdownBlock`.
3. Delete `markdownEdit.ts`, `filePreviewMount.ts`, and the old Node test.
4. Run the focused Node/Svelte checks and the named preservation suites below while iterating.
5. Update the three synchronized docs only after behavior is green.
6. Rebuild `web/dist` once, inspect the bounded asset diff, then run one final full `./verify` as the sole
   completeness claim.

## 8. Unchanged preservation suite inventory

These tests are preservation contracts, not refactor targets unless a genuine new assertion described above
is added:

- `tests/e2e/test_ticket_file_previews.py`: full route Markdown/HTML, sandbox and interactive HTML, Markdown
  height, every read-only Ticket/Chat role and preview kind, nested/self bounds, editable field/note/recap/
  proposal presentation, exact raw roundtrip, no source mode, focus/action no-write, atomic adjacency/paste/
  delete/backspace/selection, pending fetch abort, iframe clear, move/reinsert identity, and unchanged
  approval omission.
- `tests/e2e/test_live_chat_state.py::test_running_chat_preserves_unchanged_preview_subtree_and_replaces_changed_target`
  (currently line 411): for both Ticket and Chief Chat, unrelated live activity and output changes preserve
  all seven existing preview/media/iframe nodes without refetch/loading, while a changed historical source
  disconnects the old subtree and creates the replacement.
- `tests/e2e/test_flows_a.py`: continuous Markdown contenteditable shape, rendered heading/list/bold behavior,
  raw-syntax focus no-op, Escape restore, canonical real edit/save, approval, review shortcuts, and settled/
  kickoff field editing.
- `tests/e2e/test_flows_b.py`: Day Markdown edits and raw-syntax focus no-op, Sprint document Markdown edits,
  and plain name editing.
- `tests/e2e/test_chat_images.py`: immediate and reloaded managed Chat image presentation; its persisted
  Markdown reference remains unchanged.
- existing scrollbar/preview CSS coverage and backend Chat-image persistence tests remain untouched.

Before full verification, run `npm --prefix web run check`, `npm --prefix web test`, and focused
`.venv/bin/pytest` invocations for the four named e2e files above. Run `npm --prefix web run build` only at
the checked-in bundle step. Do not create a second test harness. The final claim is one clean, fully
captured `./verify` run after the built bundle and docs land.

## 9. Documentation and built assets

Update `docs/frontend.md` and the synchronized frontend/file-preview sections in `docs/systems.md` and
`docs/systems.html` in the same change. State plainly:

- product wrappers own product interaction and save state;
- one Managed Markdown surface owns Markdown DOM, atomic preview islands, serialization, reconciliation,
  and preview lifetime; and
- `FilePreview` owns what each target does, including fetch/abort, media, HTML, actions, and recursive child
  Markdown.

Remove wording that assigns lifecycle to `MarkdownBlock`, `InlineEdit`, `markdownEdit.ts`, or
`filePreviewMount.ts`. Do not add architecture history to live docs.

Rebuild checked-in `web/dist` because FastAPI serves it. The expected asset shape is:

- `web/dist/index.html` changes only to point at the new hashed JavaScript bundle;
- `web/dist/assets/index-DzbUvGPe.js` is deleted;
- one generated `web/dist/assets/index-<new-hash>.js` is added; and
- the existing CSS and font assets remain byte-for-byte unchanged because this ticket has no style work.

If the build changes CSS, fonts, extra assets, or visible markup beyond the owner refactor, stop and inspect
before widening the allowlist.

## 10. Bounded changed-path allowlist

Implementation may change only:

```text
web/src/lib/managedMarkdown.ts                         (add)
web/src/lib/editableText.ts                           (add)
web/src/lib/filePreviewMount.ts                       (delete)
web/src/lib/markdownEdit.ts                           (delete)
web/src/components/MarkdownBlock.svelte
web/src/components/InlineEdit.svelte
web/tests/managed-markdown.test.mjs                   (add)
web/tests/markdown-edit.test.mjs                      (delete)
web/package.json
tests/e2e/test_ticket_file_previews.py                 (additive focused cases only)
docs/frontend.md
docs/systems.md
docs/systems.html
web/dist/index.html
web/dist/assets/index-DzbUvGPe.js                     (delete)
web/dist/assets/index-<generated-hash>.js             (add)
```

Explicitly unchanged and outside the allowlist: `assets/markdown.js`, `filePreview.ts`,
`FilePreview.svelte`, `FilePreviewRoute.svelte`, route/caller components other than the two wrappers,
`vite-env.d.ts`, CSS/tokens/fonts, backend/database/API/event/resource-cache code, and all preservation test
files not named as an additive target above.

Any need to change the renderer contract, preview classification or target safety, recursive bound, sandbox,
route API, visible behavior, parser behavior, backend, resource invalidation, or this allowlist returns to the
orchestrator before implementation.
