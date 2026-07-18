# S2b implementation — Codex diff review (round 1)

Reviewer: Codex `gpt-5.6-sol`, reasoning effort high, read-only sandbox. Target: the complete
net S2b diff vs HEAD `fe9e2b5` (the `initiative_planning` worker-type noise, `web/dist/*` build
artifacts, and the stray `vps-agent-gui-research` doc excluded from scope). Reviewed against the
binding contract, the reviewed plan (§9 allowlist, §10 rulings), and the landed S1/S2a code.

The orchestrator independently verified EVERY finding against the code before acting. All seven
are correct; none refuted.

## Findings (all confirmed with primary evidence)

**S2B-OWN-001 — BLOCKER — fresh-binding persistence is not fail-closed (two-owner hazard).**
CONFIRMED. In `employee_child_pool.py`, `_notify_stored_session_bound` (the F5 callback) runs
OUTSIDE the spawn cleanup guard. First-create: the callback (~:295) runs after
register_child/session-create succeed but outside the `except BaseException` (:279-287); if it
raises, the transport is alive + registered and never torn down, and the record never reaches
`_records` — a later spawn resumes the same durable session → TWO live owners. Rebind (~:351):
in-memory advances before the callback; a failed persist leaves in-memory fresh but the DB stale
→ restart re-adopts the old key → fork. Fix: persistence part of guarded publication; fail closed.

**S2B-CATALOG-001 — BLOCKER — the skill picker uses an invented catalog shape.** CONFIRMED. S2a
delivers `CatalogResultEvent.payload_json` as the native `commands.catalog` result verbatim
(`neutral_downstream_session.py:363-368`). The real native shape (`shared_gateway._build_catalog`,
`:980`) is top-level `pairs` + `skill_count` + `categories[].pairs`; skills are `pairs[-skill_count:]`.
`ChiefNeutralPane.svelte:111-122` parses `categories[].items[{kind, trigger}]` — a shape that does
not exist. Real Hermes → zero skills; the scripted child fakes the invented shape so Playwright
passes falsely. Fix: parse the native shape in both the pane and the scripted child/fixtures.

**S2B-ACC-001 — MAJOR — four named acceptance tests don't exercise their behavior.** CONFIRMED.
Interrupt asserts only composer-enabled (not interrupted-turn). The 4009 test never clicks compact
and never asserts a failure. The child-reset test sends two ordinary messages (no death/reset).
The boot-raises test supplies a VALID composition and only spies the helper is called (never makes
boot raise). `./verify` stays green while required behavior is absent. Fix: drive the real paths.

**S2B-CAP-001 — MAJOR — no meta retry; missing/non-boolean capability treated as disabled.**
CONFIRMED. `capabilities.ts:26` `meta.relay_chief_enabled ? "enabled" : "disabled"` maps a
missing/non-boolean key to legacy (should be error). The routes' error branch has no retry. Fix:
only `=== false` → disabled; else error; add a retry affordance.

**S2B-NEW-001 — MINOR — newConversation() doesn't clear ephemeral state on send.** CONFIRMED.
`neutralPane.ts:402` only sends; stale in-flight/thinking/tools/question/approval remain visible
during close/create/history. Fix: clear + emit before send (incoming empty snapshot authoritative).

**S2B-READY-001 — MINOR — `data-neutral-ready` fires on socket-open, not first history.**
CONFIRMED (`ChiefNeutralPane.svelte:89`). Defeats the readiness barrier; Playwright can race.
Fix: gate the marker on first history_snapshot applied.

**S2B-ROUTE-001 — MINOR — BoardRoute eagerly builds the legacy Chief status resource.** CONFIRMED
(`BoardRoute.svelte:21`) under enabled/unknown/error — a Chief-addressed status call falling
through to the worker gateway (no session spawn, but violates the disabled-only boundary).
`ChiefOfStaffRoute` is already lazy/correct. Fix: lazy + dispose in the disabled branch only.

## Confirmed sound by the reviewer (not to be touched)
Two-owner wiring (flag-on builds no Chief gateway, omits the mapping, guards start/continue/pause/
clarify before gateway capture, settles stale recovery, calls `_assert_single_chief_owner` in both
compose branches); the flagged body-frame drop (explicitly pre-authorized; legitimate events are
`params.type` notifications, only RPC responses carry `result`/`error`); nominal `/new`
(serialization, close-before-create on the pool transport, returned-live bootstrap, empty history,
executor, intact denylist); F5 read/write symmetry + transactionality (only the failure handling is
the defect); the reactivity boundary (no resource cache, no Panels transcript read, composer never
disabled, no model picker/runnable commands, picker inserts); no forbidden schema/`minds/`/`runtime/`
changes; the single `test_human_chat_turn.py` signature-snapshot line accepted as a necessary,
non-behavioral consequence of the mandated ctor param.

## Verdict (round 1)
Not sound to integrate; **S2B-OWN-001 and S2B-CATALOG-001 must clear first** (the MAJORs/MINORs
alongside). All seven dispatched to the implementer as one fix round. One confirming Codex round
to follow (D-codex-loop-cap).

---

# Round 2 (confirming) — Codex re-review + orchestrator resolution

D-codex-loop-cap reached (2 rounds). Codex confirmed 6 of the 7 round-1 findings RESOLVED with
no regression (S2B-OWN-001 fail-closed on both first-create and rebind; S2B-CATALOG-001 native
`pairs`/`skill_count` shape in the pane + scripted child + fixtures; S2B-CAP-001 boolean-only
mapping + working retry; S2B-NEW-001 clear-before-send; S2B-READY-001 gated on first history;
S2B-ROUTE-001 lazy disabled-only status). The round-1 SOUND areas remained sound (two-owner
wiring, body-frame drop, nominal `/new`, reactivity boundary, no forbidden changes). It raised two
residuals, both precise and bounded — resolved by the orchestrator directly (no third Codex round,
per the cap; these are test-scoped closures of the reviewer's own points, not open design):

- **S2B-ACC-001 (child-reset test still superficial) — FIXED.** Codex was correct: the old
  assertions checked lines already rendered before the reset and a no-echo condition that passes
  immediately, so the test could pass without the child_reset → re-attach path executing. Added a
  real RECOVERY BARRIER: the send first paints an optimistic `you` "__reset_child__" row; the test
  waits for that row to APPEAR (send landed) then to DISAPPEAR (the post-reattach history snapshot,
  which never received the never-completed reset-cue turn, REPLACED the transcript — only reachable
  via child death → child_reset → re-attach). Verified: the strengthened test passes.
- **S2B-ALLOW-001 (new `capabilities.test.mjs` not in §9) — FIXED.** Codex was literally correct —
  §9 enumerates `web/tests/neutral-pane.test.mjs` and no separate capabilities file. Folded the
  capability assertions (boolean-only mapping, missing/non-boolean → error, markError, retry
  success/failure/missing-key) into `neutral-pane.test.mjs`; removed the separate file and its
  `web/package.json` script entry. Verified: the folded suite passes; the completeness test stays
  green.

Both fixes made directly by the orchestrator (test-only, mechanical; two implementer agents had
already died mid-run on this ticket, so a fresh agent for two tiny test edits was poor risk/reward
— noted per CLAUDE.md's "too small to be worth a ticket" allowance).

## Verdict (round 2 / final review)
All 7 round-1 findings resolved; both round-2 residuals resolved and independently verified green.
Codex loop cap reached. Sound to integrate pending the parent's canonical `./verify`.
