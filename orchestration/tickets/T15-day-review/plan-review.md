# T15 plan review — codex findings + orchestrator dispositions

Raw codex output: `plan-review-raw.txt` (same folder). Verdict there: VIOLATIONS: 6.
Disposition summary: **all six accepted**; none structural — bound as plan.md §12 amendments A1–A6. The plan's architecture (component split, grant discipline, chat transcript mechanism, smoke determinism) survives review unchanged.

| # | Finding (condensed) | Disposition |
|---|---|---|
| 1 | `withAttr(...)` used at plan.md §3 for the empty-queue line but never defined anywhere (not in the helper list, not in T14 components). Empty Review would throw. | **ACCEPT.** A1: no new helper — screens-review.js builds the quiet line, calls `setAttribute("data-review-empty", "")` on it inline. |
| 2 | `vanished` is a never-reset module set filtering canonical `/api/queues` entries; a transient detail-fetch failure (network blip, brief 500) would hide a live approval for the whole session — against SPEC §10's render-from-server-JSON rule. | **ACCEPT** (the permanence is the defect; note the sibling `skipped` set is fine — Skip is spec'd behavior and reload-transient). A2 removes `vanished` entirely and replaces the failure/stale paths with a non-hiding, non-looping design: detail-fetch rejection renders an inline error surface with a Skip control (human moves on; nothing auto-hidden, no auto-refetch loop); the staleness guard auto-skips once and renders the empty quiet line rather than looping if the same entry re-stales after a wrap. |
| 3 | Double-fire window: proposalCard re-enables Accept when the POST settles, but the card is only replaced by the debounced WS flush (~250ms+) — a second click could land in the gap. Plan claimed this couldn't happen. | **ACCEPT.** A3: every mutating control in the NEW components/screens stays disabled after success (the flush re-render replaces the DOM anyway) and re-enables only on rejection (so the human can correct and retry). T14's fieldEditor keeps its own behavior — foundation is law. |
| 4 | Self-check `grep -c innerHTML assets/*.js` totals 1 is factually wrong (comment mentions in components.js:2 and markdown.js:1 already match). | **ACCEPT.** A4: the fence is zero `innerHTML` occurrences in the two new files and in the components.js/app.css diff — not a repo-wide count. |
| 5 | Malformed smoke snippet `page.goto(base)"/"`. | **ACCEPT.** A5: `page.goto(base + "/")` per the T14 pattern. |
| 6 | Smoke (a) "exactly 2 `[data-node]` rows" — bare `[data-node]` also matches the root row; demo yields 3. | **ACCEPT.** A6: child-count assertions select `.plan-node--child` (2); total `[data-node]` = 3. |

No refutations. No re-plan needed: findings 1/5/6 are blueprint typos, 4 is a self-check correction, 2 and 3 are behavioral tightenings that strengthen the §4.4.7 surface (no hidden approvals, no double-submit) without altering any component signature or the data-* contract.
