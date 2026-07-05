# T16 implementation review — codex findings + orchestrator dispositions

Raw transcript: `impl-review-raw.txt` (codex exec, 143k tokens). Verdict: NEEDS-CHANGES with
exactly 2 findings, BOTH confined to smoke.py fallback paths — codex explicitly found **no**
endpoint/body mismatches, no T16 innerHTML, no CSS token violations in the T16 sections, no
D11 ownership violations, and none of the specifically-hunted JS bugs (link far-end anchoring
A6, grant "none" → current-state mapping A3, requireGrant gating A4, day-id date slicing).

## Dispositions (both fixed by the orchestrator directly — smoke.py is T16-owned, 2-line scope)

1. **should-fix — chat check could pass vacuously (smoke.py:371).** The try/except fell back to
   a bare `/api/chat/{id}/status` availability check, so a broken chat input/send/reply would
   still pass. ACCEPTED — the fallback is removed; fill → send → wait for
   `[data-chat-msg="planner"]` containing "echo: smoke hello" is now a hard assertion (test mode
   is online by construction: EchoGatewayAdapter).

2. **nit — copy fallback marker loop too weak (smoke.py:275).** The clipboard-unreadable fallback
   checked only success:/recap:/links: of the §10 copy block. ACCEPTED — the loop now pins all
   eight markers: state:, priority:, success:, approach:, plan:, result:, recap:, links:.
   (The primary path — byte-for-byte clipboard comparison — is what actually runs; ok 06.)

## Post-fix verification (fresh run by the orchestrator)

- `.venv/bin/ruff check orchestration/tickets/T16-board-ticket/smoke.py` — All checks passed.
- `.venv/bin/python orchestration/tickets/T16-board-ticket/smoke.py` — SMOKE PASS (11 checks),
  chat and clipboard both on primary paths (ok 06 byte-for-byte, ok 11 echo reply).
