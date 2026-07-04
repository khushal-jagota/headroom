# Overview

Date: 2026-07-03

## Brief Take

Microphone support is functionally done: built, device-tested, and committed locally. The remaining gap is boring but load-bearing: prod xAI key, then push `main`; naturally the last mile is a tiny env-var toll booth.

## Watchout

Do not reopen mic design unless something breaks in prod. Placement, button weight, states, waveform motion, and provider choice are decided; other carryover should stay secondary until the mic publication gap is closed.

## If Today Lands

A good version of today turns mic support from local-built into actually shippable: prod can transcribe via xAI, and local `main` is no longer hoarding the work like a dragon with a branch.

## Supporting Context

- Chat composer voice input is complete: mic in the empty-field send slot, recording footer with live waveform/timer, transcript inserted as editable text, normal chat send path preserved.
- Provider decision is xAI Grok batch STT behind a swap-cheap seam; Groq/OpenAI/Soniox were researched and Grok was live-smoked before committing.
- UX exploration is recorded as decided in the ux-exploration record: placement, button weight, states, and waveform motion.
- Recent local commits include the server-side xAI endpoint, ConvexError registration, voice-input state machine, composer wiring, and UX exploration docs; they are still only on local `main`.
- Morning cron closed/reconciled `2026-07-02` and left `2026-07-03/` missing for Stage 2 direction.
- No later user-shaped Stage 2 direction was found before this afternoon failsafe.
- `vylo-convex` has no post-cutoff commits, but dirty/untracked landing and durable-personas files moved after 5am; those are current-day review/progress context.
- PWA/push remains sprint work, but no evidence pulled it into today.

## Sources Checked

- User update on microphone support ship state
- 2026-07-02 tracker / overview / workspace
- Current sprint tracking
- Recent session browse/search since the morning closeout
- `vylo-convex` git status/log and dirty-file mtimes
- `landing-page-gate1.md`, `durable-personas-exploration.md`, use-case reveal-list mockup
