# AD07 contract lock

The orchestrator generated this lock after the delegated implementation plan passed independent read-only
review. AD07 concentrates the existing Markdown DOM lifecycle in one owner; it does not redesign Markdown,
editing, previews, or product behavior.

## One owner and exact public API

`web/src/lib/managedMarkdown.ts` is the only production caller of the hardened renderer and the only owner
of rendered Markdown DOM, anchor-to-`FilePreview` mounting, editable atomic preview islands, Markdown
serialization, reconciliation, empty state, and teardown. It exports exactly:

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

Mode is immutable. Source normalization is private: nullish values become `""`; all others use `String`.
Read-only `visited` inputs are copied. A destroyed surface is idempotently inert and cannot mutate or remount.
No renderer, serializer, registry, cleanup callback, observer, or source-token helper is exported.

## Read-only identity and editable reset

A read-only surface compares the complete normalized `(source, emptyText, depth, visited values in order)`
tuple. Its first update always paints. An identical later update is a complete no-op and preserves the host
child, mounted previews, media/iframe identity, and in-flight fetches. A changed tuple fully repaints and
disposes each replaced preview once.

An editable surface has an initially-unset `lastPaintedSource` plus `dirtySincePaint`. Its one host input
listener sets dirty. The first update, including an empty value, always paints. A pristine same-source update
does nothing. A dirty same-source update repaints, because restoring generated DOM after an edit cannot use
source equality alone. `hasChanges`, `read`, and `refreshEmptyState` do not repaint or clear dirty state.

`InlineEdit.svelte` retains focus, blur, save, error, retry, keyboard, Escape, and plain-versus-Markdown
interaction. It owns a pending Markdown save value so a failed save remains retryable even though the
surface repaint clears its dirty flag. `MarkdownBlock.svelte` remains the read-only product wrapper. Both
wrappers create/update/destroy a surface but own no Markdown DOM or preview lifecycle internals.

## Serialization, preview lifetime, and deletion

The current serializer moves privately, without reinterpretation, into the owner. It continues to emit the
renderer-authored exact source token for an atomic preview island, ignore generated descendants and caret
guards, and preserve the existing paragraph, heading, list, fence, inline-code, emphasis, anchor, whitespace,
adjacency, paste, and selected-deletion behavior. Editable Markdown remains one continuous
`contenteditable`; there is no source mode or new control set.

The owner creates at most one editable `MutationObserver`, only after an editable preview exists. Reconcile
after the mutation batch against final `host.contains(slot)`: moving or reinserting a slot inside the host
preserves its component and node identity; true removal unmounts once, aborts pending preview fetches, and
clears HTML iframe content. `destroy()` removes the input listener, disconnects the observer, and unmounts
every remaining preview exactly once.

## Existing boundaries, deletion set, and preservation

`assets/markdown.js` remains the hardened parser/renderer and sole user-derived `innerHTML` writer.
`filePreview.ts`, `FilePreview.svelte`, and `FilePreviewRoute.svelte` keep target safety, preview-kind
classification, recursive bounds, fetch/abort, media, iframe, action, and full-preview behavior. The Managed
Markdown owner only converts rendered anchors through the existing target boundary and mounts the existing
component. Every one of the seven preview kinds and ticket/chat/external target shapes remains unchanged.

Delete `markdownEdit.ts`, `filePreviewMount.ts`, their old Markdown lifecycle exports, and their old frontend
test. `editableText.ts` may contain only the reviewed plain/common contenteditable helpers. No compatibility
alias remains. Static tests must prove one renderer, mount/unmount, observer, source-token, and serializer
owner.

Implementation may change only the exact path allowlist in the reviewed plan. Backend, database, API,
events, resource invalidation, renderer contract, target classification, recursive bounds, sandbox, CSS,
visible interaction, AD08, and AD09 remain outside AD07. Checked-in `web/dist` is rebuilt only from the
reviewed source; CSS and fonts remain byte-identical. Any need to widen this boundary returns to the
orchestrator before work continues.
