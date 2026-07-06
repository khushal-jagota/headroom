# W1 plan review — codex + orchestrator dispositions

Reviewed artifact: `orchestration/tickets/W1-minds-primitive/plan.md` (1292 lines).
Reviewer: codex exec (gpt-5.5, read-only, high reasoning) + orchestrator sense-check.
Raw codex output: `plan-review.out` (working log; final findings quoted below).

## 1 · Orchestrator sense-check (independent, done before codex verdict landed)

Checks I ran myself against the real gateway source (`~/.hermes/hermes-agent/tui_gateway/`,
read-only) and the spike doc:

1. **Protocol claims verified at source.** Event envelope + payload-key omission
   (`server.py:803-807` `_emit`), `gateway.ready` without `session_id` (`entry.py:316-322`),
   `-32700` with `id: null` (`entry.py:329-335`), stdin-EOF clean exit (`entry.py:344`),
   `_ok`/`_err` response shapes (`server.py:867-872`), `session.create` result carrying
   `stored_session_id` (`server.py:4417`), `session.resume` → 4007 (`server.py:4599`),
   `prompt.submit` → `{"status":"streaming"}` / 4009 (`server.py:6331-6332`, return near 6384),
   `message.complete` payload `{text, usage, status}` (`server.py:6770-6805`),
   `_LONG_HANDLERS` + 4-thread pool making out-of-order responses real (`server.py:165-201`),
   deferred agent build on a 0.05s timer making error-event races real (`server.py:4404-4411`),
   stdout reserved for JSON (`server.py:203-207`). All match the plan's §0 table. No
   protocol-shape errors found by hand.
2. **D3 (queue keyed by caller-owned mind identity, not literal session_key).** This is the one
   real judgment call. The ticket says "one in-flight run per `session_key`"; the plan keys on a
   caller-supplied stable mind key with `session_key` riding inside the item. My disposition:
   ACCEPT — one mind == one durable session (notes.md "One mind per ticket"), so mind-key
   serialization implies session-key serialization, and it extends the invariant to step 0
   (session_key=None) where two queued creates for the same mind would otherwise race — exactly
   the race the queue exists to prevent (notes.md: busy-guard is per-process only). The ticket's
   named test ("two producers on the SAME session_key") stays literally satisfiable: a durable
   session key is a valid queue key. Amendment A2 below makes the test use a session-key-shaped
   string so the ticket bullet is covered verbatim.
3. **MindQueue locking** (plan §6): submit/drain hold one lock for the pending-deque append +
   active-set check, and for the empty-check + retire; a racing submit either observes the key
   active (appends; the live worker pops it) or retired (starts a fresh worker). I walked the
   interleavings: no lost item, no double worker, no lost wakeup (`wait_idle` uses the same
   Condition). Sound.
4. **run_step status mapping** (plan §5 table): all terminal paths covered — the three
   `message.complete` statuses, `error` event, child EOF, 4007 resume, 4009 submit, other RPC
   errors, transport errors, spawn failure; child reaped in `finally`. Matches the ticket's
   mapping. Sound.
5. **Hermeticity**: fake replaces only the OS process behind a `ChildProcess` protocol; the same
   reader/router runs. `smoke.py` under `src/` is never collected (pytest `testpaths=["tests"]`),
   and the verify skip-scan walks `tests/` only. No pytest-reachable real subprocess in the plan.
6. **Scope vs ticket (the pruning check)**: the plan's file list is exactly the ticket's owned
   files + the ticket-named smoke script and test file; no wiring into server/DB/dispatcher/CLI;
   no extra modules. The plan is long because it is precise (pseudocode + 28 tests), not because
   it over-builds. Nothing to cut; Amendment A1 pins the file boundary as binding.

## 2 · Codex findings + dispositions

Codex verdict: **REPLAN**, on three findings — all on one axis, the queue-key contract (plan D3).
Explicitly clean per codex: protocol-shape errors, `_drain`/`submit` locking, `run_step` status
mapping, test hermeticity, mypy/ruff risks, scope/owned files.

### Finding 1 — [BLOCKER] "D3 violates the ticket's literal `session_key` invariant by keying
serialization on a caller-owned mind id" (plan.md:63-73, 689-696 vs ticket.md:45-48,
notes.md:99-106; same durable key submitted under two caller keys can overlap).

**Disposition: ACCEPT.** The ticket is law: `queue.py`'s contract is "one in-flight run per
`session_key`". The planner's "mind identity" key was an extrapolation beyond the ticket. The
queue *mechanics* stand (codex agrees they are race-free); what changes is the contract: the key
IS the durable Hermes `session_key`. With that contract, "same key under two different keys" is a
caller bug by definition — the same misuse class as any keyed lock — not a designed-in hazard.
→ Amendment A2.

### Finding 2 — [BLOCKER] "The step-0 justification still permits two durable Hermes sessions
for one mind" (queued items capture `session_key=None`; both would `session.create`).

**Disposition: ACCEPT the observation; resolve by descoping the claim, not by building
convergence machinery.** W1 makes no claim to solve step-0 identity: nothing invokes the queue
this wave, and the ticket explicitly assigns key persistence to "the caller in a later wave"
(ticket.md, runner section). The plan's "session_key travels inside the queued item" framing is
withdrawn — the queue is generic over the item type and prescribes nothing about item contents.
The real fix belongs to wave 3's wiring: the injected run callable should resolve the mind's
CURRENT stored session_key at execution time (not capture it at enqueue time), so a queued
step-0 pair converges on the first created key; alternatively System B routes kickoff specially.
Recorded as an explicit boundary in the queue module docstring and flagged to the integrator in
report.md. Building convergence into W1's queue would exceed the ticket (and the owner's
standing instruction not to over-build). → Amendment A2 (docstring), report.md concern #1.

### Finding 3 — [MAJOR] "The test plan claims queue coverage but tests the wrong invariant"
(tests use `"ticket:1"`/`"ticket:2"` keys; ticket.md:63-65 says SAME `session_key`).

**Disposition: ACCEPT.** Tests 19-21 are re-keyed to durable-session-key-shaped strings so the
ticket's bullets ("two producers on the SAME `session_key`" / "DIFFERENT keys") are covered
verbatim. The suggested "two step-0 items converge to one persisted key" test is declined for
W1 — there is deliberately no convergence machinery in W1 to test (finding 2 disposition); noted
for wave 3's suite. → Amendment A3.

### On the REPLAN verdict

Not taken as a structural re-plan: every part of the plan codex judged clean survives untouched
(file set, public APIs, ChildProcess/SpawnFn seam, frame router, run_step mapping, fake, config,
smoke, 25 of 28 tests). The three findings share one root — D3's key *contract* — and are fully
resolved by the binding amendments below, which restore the ticket's literal contract. Patch,
not re-plan.

## 3 · Binding amendments

Appended to `plan.md` as "§12 Binding amendments (post-review)" — the implementer follows
plan.md WITH §12; where §12 conflicts with earlier sections, §12 wins.
