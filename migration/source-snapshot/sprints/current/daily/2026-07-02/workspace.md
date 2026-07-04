# Workspace

Date: 2026-07-02

## Necessary Calls

- 2026-07-03 Stage 2 direction.
  - Afternoon failsafe later created `2026-07-03/` as a conservative scaffold.
  - Use the unresolved tickets below and the 2026-07-03 scaffold as carryover candidates, not as a user-approved plan for today.

## Tickets

- Landing page Gate 1: shippable enough to push `main`.
  - Ticket ID: ticket-20260702-landing-page-gate-1
  - Chat ID: 20260702_114500_0ec57a
  - Readiness: In Progress
  - Mode: Paired
  - Priority: P0
  - Project: Vylo
  - Body: Ship a landing page that meets Gate 1: good enough to push `main` to prod, separate from Gate 2 “genuinely good landing page”.
  - Current state: hero copy/nav brandmark work is in the dirty working tree, and `.claude/plans/landing-page-gate1.md` captures the next clarity-block route.
  - Success: the page is clear enough that a cold visitor roughly understands what Vylo is doing; designed enough to show craft; visually interesting enough not to bore; and promising enough to provoke curiosity.
  - Next: shape the clarity block, decide whether any extra section earns a place, settle the “strangers” wording, and align stale page metadata.
  - Boundary: Gate 1 only; do not expand into full landing-page excellence.

- Add microphone support for Vylo.
  - Ticket ID: ticket-20260702-microphone-support
  - Chat ID: 20260702_114233_129b0c
  - Readiness: In Progress
  - Priority: P1
  - Mode: Supervised
  - Project: Vylo
  - Body: Add microphone input to Vylo chat so users can provide audio instead of typing, without making this a voice-to-voice conversation.
  - Current state: local `main` has committed voice-input/transcription work; `.claude/plans/chat-mic-voice-input.md` reports design closure, xAI STT selection, implementation details, and real-phone validation done on 2026-07-02.
  - Carryover: user closeout/ship state still needs confirmation before this becomes `Done` or disappears from carryover.
  - Boundary: audio input inside chat only; no voice-to-voice conversation or broad voice-product design.

- App typography pass.
  - Ticket ID: ticket-20260701-app-typography-pass
  - Chat ID: 20260702_035215_6b26c2
  - Readiness: Ready
  - Priority: P2
  - Mode: Paired
  - Project: Vylo
  - Carryover: 2026-07-01
  - Body: App typography pass.

- First durable-personas exploration.
  - Ticket ID: ticket-20260702-durable-personas-first-exploration
  - Chat ID: 20260702_120132_6cf7a4
  - Readiness: In Progress
  - Priority: P1
  - Mode: Paired
  - Project: Vylo
  - Body: Explore what durable personas should mean for Vylo before turning it into implementation work: personas already exist as separate things in the current repo, and this is about how they should be assigned to real individuals, made durable, connected to linked material, and allowed to compound over time.
  - Current state: `.claude/plans/durable-personas-exploration.md` captures repo grounding, value layers, compute-on-write psychology, automatic durable-person creation, and unresolved model questions.
  - Still open: future sources, persona-based testing, psychology-update behaviour, history/on-write surface, rules placement, commitments, and standing/their-model-of-you.
  - Boundary: understand and sharpen the sprint item; do not turn this into schema implementation planning or child tickets yet.
