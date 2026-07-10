# Preview lifecycle regression — 2026-07-10

## User-visible failure

An atomic file preview could disappear while Markdown remained focused. Pressing Enter at the preview boundary reproduced it deterministically.

## RED evidence

The browser regression stored a reference to the mounted slot, pressed Enter before it, and then observed:

- the same slot element was still connected;
- the slot was still inside the same Markdown editor;
- the slot's `data-file-preview` descendants had fallen from one to zero.

This proved that rendering, persistence, and async resolution were not the failing seams. The mounted Svelte component had been explicitly destroyed while its host remained live.

## Root cause

Chromium can implement a structure-changing `contenteditable` edit by removing and reinserting existing nodes. `filePreviewMount.ts` treated every slot mentioned in a `MutationObserver.removedNodes` record as permanent deletion. It therefore called Svelte `unmount` on a slot Chromium had already reinserted into the editor.

## Fix

Unmount only when the slot is no longer contained by the editor root when the mutation callback runs. Moves within the same editor retain the component; real deletion still unmounts it.

## Proof

- The regression was observed RED before production code changed.
- It now performs three structure-changing Enter edits plus an ordinary click and keeps the same connected preview visible.
- The existing deletion test still proves a truly removed slot aborts a pending Markdown fetch and clears an HTML iframe.
- The focused Node, Svelte, and 27-test affected browser suite passes.
- Live validation on `t_c5sb8b0h` moved the first result preview's exact slot with Enter: all four previews remained mounted; Escape restored the canonical field with four previews and no edit controls.
- Independent Codex `gpt-5.5` review returned `NO VIOLATIONS`.
- Final repository-wide verification passed: Ruff, Mypy, 209 unit tests, frontend check/build,
  47 E2E tests, and `VERIFY: PASS`.
