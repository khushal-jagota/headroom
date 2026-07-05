# T14 plan review — codex output + orchestrator dispositions

## Process note (deviation from strict step order, directed)

Two consecutive `codex exec` runs hung with zero output. Root cause found on the second: codex printed "Reading additional input from stdin..." — in a background shell stdin is an open pipe that never closes, so codex waited forever. Fix: `< /dev/null` on the relaunch. Total loss ~50 minutes. The team lead directed the pipeline to proceed: the orchestrator's sense-check (below) was written and the implementer dispatched while the stdin-fixed codex review runs in parallel; codex findings are dispositioned in this file when they land and any real ones are folded in before/with the diff review. The orchestrator had already independently verified the plan's load-bearing server-side claims (§ below) before proceeding, so the plan did not go to implementation unreviewed.

## Orchestrator's independent verification (done before implementation dispatch)

Checked the plan's claims directly against source:

1. `POST /api/ideas` → `_create_idea` (src/planner/sprints/api.py:79-94): exactly one INSERT + exactly one `append_event(idea_created)` per call, title required, no preconditions. The smoke's one-call-one-event assumption holds. VERIFIED.
2. `queues_view` (src/planner/tickets/views.py:391-402) returns `{approvals, pickup, overdue}`; `approvals` is a list — `approvals.length` is the badge count. VERIFIED.
3. WS payload (src/planner/core/ws.py:60-62): `{"events": [...], "cursor": cursor}`, non-empty batches only, continuation when batch == events_read_limit. Matches the plan's bus semantics, including the coalescing/starvation analysis (server poll 300ms > debounce 250ms). VERIFIED.
4. Env overrides the smoke uses all exist (src/planner/core/config.py): PLAN_WS_POLL_MS (147), PLAN_DISPATCHER_LOCK_PATH (154-156), PLAN_LOGS_DIR (157), PLAN_FAKE_NOW (131). `load_config(path: str | None, env: Mapping | None)` (126) accepts the explicit-env pattern. VERIFIED.
5. Serve wiring the smoke mirrors (src/planner/cli/main.py:55-72) matches plan step 3. VERIFIED (read-only; cli/ is another ticket's file).
6. Nothing in tests/ pins `_SHELL` or `GET /` (grep clean); ruff covers orchestration smoke scripts (T10's passes) so smoke.py must be ruff-clean — the plan already says so. VERIFIED.
7. Baseline gates before any T14 change: ruff clean, mypy clean (83 files), `pytest tests/unit -q` exit 0. VERIFIED.

## Orchestrator sense-check findings → plan amendments

**S1 — D11 item 1 says "six screen links"; the plan ships five (accepted, recorded as a deliberate deviation).** `#/ticket/<id>` needs an id, so a permanent "Ticket" nav link has no honest target; SPEC §10 reaches Ticket via "card click opens Ticket" (§10.3) and the per-item "link to the full Ticket" (§10.2), and SPEC §10's own bar forbids adding what nobody needs. D11's phrasing is loose drafting (the same D11 entry notes SPEC's "five screens" typo). Per playbook, the contract is not amended: the deviation is recorded here and in the final report for the integrator. Reversal is cheap (one entry in `appShell`) if the top level reads D11 literally.

**S2 — Type token category (accepted).** The ticket's own owned-files line names "five-size type scale" as tokens.css content, and the app.css zero-literal-sizes constraint makes a type scale inexpressible without tokens. The five `--type-*` tokens make the five-size cap grep-auditable. Font-stack tokens ride along under "the app's entire personality must be tunable by editing this one file". Accepted as the argued evidence-of-need case.

**S3 — Ticket acceptance says "node --check green on all six JS files"; the ticket's owned-files list contains exactly five .js files (config, api, markdown, components, app).** Ticket typo (miscount, possibly counting a CSS file); the binding constraint line says "Every JS file passes node --check" and D9 fixed the instrument to every *.js under assets/. Gate applied: all five JS files green. Recorded; no contract change.

**S4 — Amendment (binding): the literal word "await" may not appear anywhere in the JS files, including comments** — the plan's self-check grep `\b(import|export)\b|await` must stay empty. The plan's code is Promise/.then-based already; this pins comments too.

**S5 — Smoke robustness (binding):** cursor assertion must be `== 3` exactly as planned (fresh DB, three idea posts, no other event writers in the smoke's request set — /api/meta and /api/queues are read-only, placeholders fetch nothing). If the coalescing check (two rapid POSTs ⇒ one flush) ever flakes, widen the settle sleep; never weaken the exactly-one assertion.

Everything else in the plan: accepted as written. The amendments were appended to plan.md as the binding amendments section before implementation dispatch.

## Codex findings + dispositions

Codex (gpt-5.5, xhigh reasoning, read-only sandbox) completed after the stdin fix; full transcript in plan-review-raw.txt. Verdict: `VIOLATIONS: 5` (4 MAJOR, 1 MINOR). Clean categories confirmed by codex: server/API shapes (meta, events WS, POST /api/ideas one-event, queues_view), markdown/XSS ("no concrete XSS hole found"), invalidation-bus discipline ("no client entity store or WS payload reconciliation").

**C1 (MAJOR) — D11:53 "six screen links" vs the plan's five.** UPHELD AS RECORDED DEVIATION, NOT FIXED. Same finding as orchestrator S1, ruled before codex reported: `#/ticket/<id>` needs an id, SPEC §10 reaches Ticket via cards/links, and a permanently inert nav entry fails SPEC §10's add-nothing bar. D11 is not amendable by this ticket (playbook: contracts are law; concerns go in the report). Surfaced to the integrator in report.md; reversal is one entry in `appShell`.

**C2 (MAJOR) — ticket.md:16 "component functions returning DOM nodes" vs `appShell()` returning a handle object.** ACCEPTED, FIXED. `appShell()` now returns the shell DOM element itself with `content`/`setReviewBadge`/`setActiveNav` attached as properties on the node (components.js), and app.js appends `shell` directly. All seven component functions now literally return DOM nodes. Re-verified: node --check green, smoke 7/7.

**C3 (MAJOR) — smoke.py outside the ticket's owned files.** REFUTED. The ticket's acceptance section itself mandates a scripted smoke; D2 places all per-ticket pipeline artifacts under `orchestration/tickets/<TICKET>/`, and T10 established the smoke.py convention there (orchestration/tickets/T10-domain-apis/smoke.py). The dispatch for this ticket names the smoke as a gate deliverable. Not product code; not on any import path.

**C4 (MAJOR) — two-adjacent-POSTs ⇒ one-flush assertion called timing-racy.** ACCEPTED AS DOCUMENTED RESIDUAL RISK, NOT CHANGED. For the assertion to fail, the second batch would have to arrive more than debounce (250ms) after the first — i.e. two sequential localhost POSTs separated by >200ms with the server polling at 50ms. Typical separation is <20ms. The plan pinned this exact risk and its only sanctioned remedy (widen the settle sleep; never weaken the exactly-one assertion — amendment A5). A fully deterministic variant would need server-side batching control that is outside this ticket's owned files. Two consecutive full smoke runs passed.

**C5 (MINOR) — smoke proved placeholders only on Day and Board.** ACCEPTED, FIXED. The final smoke check now iterates all six route shapes (#/review, #/board, #/sprint, #/backlog, #/ticket/t_x, #/day) and asserts the placeholder on each. Re-run: `SMOKE PASS (7 checks)`.

Both fixes were applied by the orchestrator directly (playbook-sanctioned small fixes), then all gates re-run fresh: node --check x5 green, import/export/await grep empty, ruff clean, smoke 7/7 PASS.
