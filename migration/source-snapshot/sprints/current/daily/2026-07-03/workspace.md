# Workspace

Date: 2026-07-03

## Necessary Calls

- Choose today’s actual lane from the failsafe scaffold.
  - [assumption] No user-shaped Stage 2 direction was found after the morning closeout, so these tickets are carried as unresolved candidates, not as a confirmed plan.
  - Recommended call: close the mic publication gap first, then pick one main carryover lane. Landing Gate 1 and durable personas have current-day repo movement; typography is preserved but should not win by inertia.

## Tickets

- Publish microphone support.
  - Ticket ID: ticket-20260703-mic-prod-publish
  - Readiness: Ready
  - Mode: Manual
  - Priority: P0
  - Project: Vylo
  - Body: Turn completed local microphone support into a shippable prod state.
  - Current state: voice input is built and device-tested; local `main` contains the xAI endpoint, ConvexError registration, state machine, composer wiring, and UX exploration commits, but they have not been pushed.
  - Success: prod has the xAI key set, local `main` is pushed, and the chat composer can transcribe through the normal chat path in the deployed environment.
  - Boundary: publication only; do not reopen provider/design exploration unless prod testing exposes a real fault.

- Landing page Gate 1: shippable enough to push `main`.
  - Ticket ID: ticket-20260702-landing-page-gate-1
  - Chat ID: 20260702_114500_0ec57a
  - Readiness: In Progress
  - Mode: Paired
  - Priority: P1
  - Project: Vylo
  - Body: Ship a landing page that meets Gate 1: good enough to push `main` to prod, separate from Gate 2 “genuinely good landing page”.
  - Current state: hero copy/nav brandmark work is in the dirty working tree, `.claude/plans/landing-page-gate1.md` captures the clarity-block route, and post-5am repo movement touched `LandingPage.tsx` plus a disposable use-case reveal-list mockup.
  - Success: the page is clear enough that a cold visitor roughly understands what Vylo is doing; designed enough to show craft; visually interesting enough not to bore; and promising enough to provoke curiosity.
  - Next: review/integrate the current-day landing changes, shape the clarity block, decide whether any extra section earns a place, settle the “strangers” wording, and align stale page metadata.
  - Boundary: Gate 1 only; do not expand into full landing-page excellence.
  - Assumption: carried by failsafe as unresolved/current-day context, not user-confirmed 2026-07-03 priority.

- App typography pass.
  - Ticket ID: ticket-20260701-app-typography-pass
  - Chat ID: 20260702_035215_6b26c2
  - Readiness: Ready
  - Priority: P2
  - Mode: Paired
  - Project: Vylo
  - Carryover: 2026-07-01
  - Body: App typography pass.
  - Assumption: preserved because it remains explicit unfinished carryover; not promoted above the stronger landing/personas/mic choices.

- First durable-personas exploration.
  - Ticket ID: ticket-20260702-durable-personas-first-exploration
  - Chat ID: 20260702_120132_6cf7a4
  - Readiness: In Progress
  - Priority: P0
  - Mode: Paired
  - Project: Vylo
  - Body: Explore what durable personas should mean for Vylo before turning it into implementation work: personas already exist as separate things in the current repo, and this is about how they should be assigned to real individuals, made durable, connected to linked material, and allowed to compound over time.
  - Current state: `.claude/plans/durable-personas-exploration.md` captures repo grounding, value layers, compute-on-write psychology, automatic durable-person creation, structural first-pass thinking, and unresolved model questions; the file moved again after the 5am boundary.
  - Still open: future sources, persona-based testing, psychology-update behaviour, history/on-write surface, rules placement, commitments, standing/their-model-of-you, and whether interests/knowledge/cares is its own top-level kind.
  - Boundary: understand and sharpen the sprint item; do not turn this into schema implementation planning or child tickets yet.
  - Assumption: carried by failsafe as unresolved P0 sprint work/current-day context, not user-confirmed 2026-07-03 priority.
