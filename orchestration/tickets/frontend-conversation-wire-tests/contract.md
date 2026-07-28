# Conversation wire test decomposition

## Program context

This is the second slice of the frontend simplification program. The first
slice established Vitest with ordinary TypeScript imports. This Ticket uses
that seam to remove the 2,101-line `conversation-wire.test.mjs` harness before
production Conversation modules are simplified or moved.

The program reserves one canonical `./verify` run for its final settled tree.
This Ticket must pass every focused gate named below before integration.

## Accepted outcome

The old test is gone. Its product behavior is covered by small, semantically
named Vitest suites which import the public Conversation modules through the
normal resolver.

No replacement test exceeds 600 lines. Tests should normally remain between
roughly 100 and 400 lines; a test owns one behavior domain, not an arbitrary
slice of the former file. Shared fixtures contain only typed event construction
or external fakes used by multiple suites.

The migration removes all source reading, temporary compilation, generated
modules, import rewriting, exact module inventories, and manual success
logging. Production behavior and production source do not change.

## Contract-scoped files

- `web/package.json`
- deletion of `web/tests/conversation-wire.test.mjs`
- these Vitest suites:
  - `web/tests/conversation-pending-images.test.ts`
  - `web/tests/conversation-feed.test.ts`
  - `web/tests/conversation-transcript.test.ts`
  - `web/tests/conversation-composer-asks.test.ts`
  - `web/tests/conversation-composer-delivery.test.ts`
  - `web/tests/conversation-outgoing.test.ts`
  - `web/tests/conversation-outgoing-storage.test.ts`
  - `web/tests/conversation-thread-items.test.ts`
  - `web/tests/conversation-thread-plan.test.ts`
  - `web/tests/conversation-turn-time.test.ts`
  - `web/tests/conversation-tool-presentation.test.ts`
  - `web/tests/conversation-wire-values.test.ts`
- one small typed event fixture under `web/tests/support/` if at least three
  suites use it
- this Ticket's orchestration artifacts

No package dependency, lockfile, Vitest configuration, production file,
remaining legacy suite, CSS, backend file, or browser test belongs to this
Ticket. If an ordinary import exposes a production defect, record it instead of
widening the Ticket.

## Required behavior

1. Every suite imports the real production module. Do not mock a production
   Conversation module.
2. Fakes stop at existing external seams:
   - the ports passed to `createConversationStream`;
   - injected image object-URL creation and revocation;
   - browser `window.sessionStorage`;
   - clock or randomness where deterministic outgoing identity needs it.
   Use fake timers or controlled promises rather than a real scheduling delay.
3. Pending-image coverage preserves selection order, supported media and
   per-message admission, aggregate and already-pending byte bounds before
   reading, exact-boundary admission, piece encoding, restored data previews,
   object-URL ownership, and partial-batch cleanup.
4. Feed coverage preserves sequence merge/replacement/order, transient agent
   text and tool progress, ghost-frame rejection, turn cleanup, running state,
   snapshot liveness reconciliation, current run values, incremental
   fetch-then-tail connection, reconnect closure/order, and final close.
5. Transcript coverage preserves tool start/finish reconciliation, live,
   answered, dead, and stopped-without-ending asks, streaming replacement,
   refusal and ending text, message images on both sides, legacy text messages,
   usage rows, and compaction rows.
6. Composer coverage preserves backend delivery choices, steer capability,
   backend-provided and recovered ask actions, question choices and digit
   selection, placeholder rules, commit-on-send changes, message-body fidelity,
   backend selection only during Conversation creation, model/effort
   preselection, and delivery-fate wording.
7. Outgoing coverage preserves unique identity and send time, fate notes,
   sender-message reconciliation, post-read transitions, per-Conversation
   session persistence, untrusted-storage rejection, image byte fidelity,
   storage and concurrent-image budgets, reload reconstruction, canonical
   release, and reservation cleanup.
8. Thread coverage preserves turn anchors, chronological work groups, running
   and settled folds, final-answer retention, prompts and asks outside folds,
   interrupted and tool-only turns, multiple turns, steer joining the current
   turn, stopped-turn behavior, and plan ownership/replacement/persistence.
9. Timing coverage preserves duration boundaries, turn-relative tick
   calculation, delayed/background recovery, running/settled wording, believable
   sender-clock reconciliation, rejection of seconds presented as
   milliseconds, and steer not resetting the turn start.
10. Tool-presentation coverage preserves readable structured detail, prompt
    labels, tool glyph classification, current backend payload interpretation,
    one-line and truncated summaries, shell-wrapper removal, duplicate-detail
    suppression, and disclosure decisions.
11. Wire-value coverage preserves the closed backend set, stream names,
    capability values, legacy text normalization, image-piece preservation,
    text extraction, and encoded Conversation file addresses.
12. Remove assertions whose only contract is an exact source filename, import
    spelling, physical module inventory, generated-module order, or successful
    execution log. Preserve current public interface values and behavior; this
    Ticket does not simplify product policy.
13. `npm test` runs Vitest once, then every remaining legacy suite exactly once.
    Remove only `conversation-wire.test.mjs` from `test:legacy`; the other nine
    commands retain their relative order.
14. The shared event fixture, if used, exports only descriptive typed builders.
    It does not hide assertions, expected results, or a second feed/transcript
    implementation.

## Migration sequence

Create each semantic Vitest suite while the legacy test remains green. Run that
suite directly and compare its named behavior to the owning section of the
legacy file. Delete the old file and its legacy command only after all
replacement suites pass together.

The migration is expected to reduce harness code but not necessarily total
behavioral assertions. A shorter suite is not evidence of success if it loses a
named behavior above.

## Named focused gates

- all twelve new Vitest suites together, including their typecheck tasks
- `npm --prefix web run check`
- `npm --prefix web run build`
- the complete `npm --prefix web test`
- `git diff --check`
- a scope inspection proving no replacement test is over 600 lines and no
  source-reading/transpilation harness remains

## Completion boundary

The Ticket is complete when its reviewed implementation passes every named gate
and is committed on `codex/frontend-conversation-wire-tests`. It may then be
integrated serially into `staging`. It does not change Conversation behavior,
split production modules, or migrate `conversation-rest-line.test.mjs` or
`conversation-pane.test.mjs`.
