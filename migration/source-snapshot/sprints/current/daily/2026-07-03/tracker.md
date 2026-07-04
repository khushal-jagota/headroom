# Daily

Date: 2026-07-03

## Focus

Close the microphone-support ship gap, then only return to the Vylo carryover that still earns attention.

## Todo

- Publish microphone support.
  - Set the xAI key on prod, then push local `main`; commits are local-only for now.
- Choose/narrow the 2026-07-03 lane from this failsafe scaffold.
  - [assumption] No user-shaped Stage 2 direction was found after the morning closeout; this folder exists to keep the planner valid, not to claim a confirmed plan.
- App typography pass.
  - Carry forward `ticket-20260701-app-typography-pass`; no repo/user evidence that the pass itself was completed.

## In Progress

- Landing page Gate 1: good enough to ship and push `main`.
  - [assumption] Carried because it was unfinished on 2026-07-02 and there is post-5am dirty repo context, not because today’s lane was user-confirmed.
  - Current-day context: `LandingPage.tsx` and a new use-case reveal-list mockup moved after the 5am boundary; treat as review/integration context, not completion.
- First durable-personas exploration.
  - [assumption] Carried because the sprint item is P0 and the exploration notes moved after 5am; still not complete or user-approved as today’s main lane.
  - Still open: future sources, persona-based testing, update behaviour, history/on-write surface, rules placement, commitments, and standing/their-model-of-you.

## Done

- Microphone support — built and device-tested; user confirmed it went past shaping through implementation.
  - Chat composer now swaps the empty-field send slot to a mic, recording takes over the footer with live waveform/timer, and transcripts land as editable text that sends through the normal chat path.
  - Provider decision: xAI Grok batch STT behind a single swap-cheap seam; Groq/OpenAI/Soniox were researched, and Grok was live-smoked before committing.
  - UX exploration is decided and recorded: placement, button weight, states, and waveform motion.

## Blocked

-

## Tomorrow / Carryover

- Microphone support publication is the only remaining ship gap: prod xAI key plus push.
- If this scaffold is wrong, replace it with the real Stage 2 direction and keep only the chosen lane active.
- Carry unresolved candidates only: landing Gate 1, durable-personas continuation, and app typography.

## Deferred

- PWA/push shipping cutover stays in sprint tracking for now; no 2026-07-03 direction pulled it into today.

## Notes

- Afternoon failsafe created `2026-07-03/` because the day folder was still missing after the 2026-07-03 morning Stage 1 closeout.
- Recent-session check found only the morning cron closeout before this run; no user Stage 2 direction was captured.
- Repo freshness checked: `vylo-convex` `main` is ahead of `origin/main`, has no commits since the 2026-07-03 5am cutoff, and has dirty/untracked landing/personas files modified after the cutoff. Those are current-day context, not old-day completion certificates, despite what the filesystem’s little crown says.
