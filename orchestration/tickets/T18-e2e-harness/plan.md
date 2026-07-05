# T18 plan — E2E harness + items 22–27

Deliverables: `tests/e2e/conftest.py`, `tests/e2e/test_flows_a.py`. No `helpers.py` (§C).
No asset changes (§F). Nothing else is touched; the unit suite stays untouched.

Ground truth this plan is written against (verified on disk, not assumed):

- `src/planner/core/server.py` — `StaticFiles(directory="assets")` is CWD-relative (line 149);
  test mode never starts background loops (lifespan, D6); `/api/meta` returns
  `{ui_debounce_ms, ws_poll_ms, test_mode}`.
- `src/planner/core/config.py` — every `PLAN_*` override; env beats `config.yaml` (which exists
  at repo root and is read by `plan serve` from CWD).
- `src/planner/core/adapters/registry.py` — `auto` → fakes under test mode (echo gateway);
  `PLAN_GATEWAY_ADAPTER=offline` selects `OfflineGatewayAdapter` at **boot**. Offline is a
  boot-time choice → item 26's offline half needs a second server instance.
- `src/planner/core/adapters/fakes.py` — echo reply is exactly `"echo: <text>"` (line 99);
  first minted session key is `"fake-sess-1"` (lines 96–98, per-process counter).
- `src/planner/core/clock.py` + `testmode.py` — `PLAN_FAKE_NOW` (naive ISO → local) builds a
  `TestClock`; `/api/test/set-now` exists but none of items 22–27 needs it.
- `src/planner/cli/main.py` / `http.py` — `plan serve` boots uvicorn from `load_config()` over
  `os.environ`, creates the schema itself; `PLAN_SERVER_URL`, `PLAN_TICKET_ID` (envvar on the
  positional), `PLAN_ACTOR` default `"agent"` (header always sent), `PLAN_RUN_ID`/`PLAN_CLAIM`
  only when set; `--body-file -` reads stdin verbatim (unstripped); `--json` prints the raw
  response JSON on stdout; exit codes 0/1/2.
- `src/planner/tickets/api.py` + `data.py` — R2 default grant on create is
  `ceiling=needs_success, at_cap=propose` (data.py lines 153–154); `POST /grant` and
  `POST /accept/{field}` are `reject_agents` (human = request with **no** `X-Plan-*` headers,
  per `authctx._classify`); auto-accept per `machine.auto_accept_target`: gating field and
  advance-target ≤ ceiling → auto-accept, else the proposal parks.
- `src/planner/core/ws.py` — the tailer sends **non-empty batches only**, no keepalives; a
  fresh WS with `since=0` immediately replays all pre-existing events as one batch.
- `assets/api.js` — `window.__plannerDebug = {flushes, wsOpens, cursor}`; every batch schedules
  a debounced flush; flush → `route()` re-render (screen div replaced).
- `assets/components.js` — proposal card: `[data-accept]` disabled until `grantPairPicker`
  yields both halves (lines 508–551); `[data-edit]` textarea prefilled with `proposal.body`,
  `edited_body` sent iff it differs; `[data-grant-ceiling]` select ("No further" → value
  `"none"`); `[data-grant-atcap] input[value="stop"|"propose"]` radios; chat:
  `[data-chat-panel]`, `[data-chat-input]`, `[data-chat-send]`, `[data-chat-msg="you"|"planner"]`,
  `[data-chat-offline]` rendered only when `status.available` is false (no input rendered then).
- `assets/screens-board.js` — `section[data-screen="board"]`,
  `[data-column="<state>"] [data-card][data-ticket-id]`.
- `assets/screens-ticket.js` — `section[data-screen="ticket"][data-ticket-id][data-state]`,
  `[data-field="success"]` etc., `[data-chat]`.
- `assets/screens-review.js` — `[data-review-card][data-entity-id][data-kind]`,
  `[data-review-empty]`, `[data-skip]`.
- `assets/markdown.js` — headings → `<h1..h3>`, `-` lists → `<ul><li>`, `1.` lists → `<ol><li>`,
  `**x**` → `<strong>`, paragraphs → `<p>`; block div classes `markdown markdown-block`.
- `scripts/verify_lib.py` — an item PASSes iff **exactly one** junit bare test name starts with
  `test_eNN_` and it passed; multiple anchored matches = FAIL. `scripts/verify.py` runs
  `pytest tests/e2e --junitxml=...` (plain, headless, 1800 s ceiling).
- Working Playwright drive patterns mined from `orchestration/tickets/T15…T17/smoke.py`
  (selector usage, `wsOpens` wait, grant-pair driving, chat echo, offline-second-server). Their
  in-process-thread server is **not** copied — T18 mandates a real subprocess server.

---

## 0. Pinned decisions

| Decision | Value | Why |
|---|---|---|
| Server scope | one subprocess per test (function-scoped), fresh temp DB | the ticket's stated shape; makes every assertion exact (empty board, single queue entry, `fake-sess-1`) and kills cross-test coupling |
| Server command | `.venv/bin/plan serve`, `cwd=REPO_ROOT` | real subprocess through the real CLI entrypoint; repo-root CWD because the assets mount and `config.yaml` load are CWD-relative |
| `PLAN_FAKE_NOW` baseline | `2026-07-04T12:00:00` | matches the unit suite's clock baseline; 12:00 > boundary 05:00 so the planning date is stable; no test here moves time |
| `PLAN_WS_POLL_MS` | `50` | tight tail cadence; safe because batches are non-empty-only |
| `PLAN_UI_DEBOUNCE_MS` | `50` | fast flushes. Safe: flushes can only follow events; test mode runs no background loops, so between our own mutations the DOM is quiescent. (T17 needed a *high* debounce for a freeze window; no T18 test edits UI state that a flush could clobber once the settle rule below is applied.) |
| Demo seed | **not used** | fresh entities per test via CLI/API are hermetic; avoids T16's clock-skew trap (seed stamps wall-clock while the test clock is frozen) |
| Browser | pytest-playwright's session-scoped `browser` fixture (chromium) | headless is the default; `./verify` invokes plain `pytest tests/e2e` and never passes `--headed`, so headless is guaranteed |
| Contexts | hand-rolled `browser.new_context()` via a function-scoped tracking fixture | tests need 1–2 contexts each; pytest-playwright's `context`/`page` fixtures are single-context |
| helpers.py | not created | everything is fixture-shaped; a separate module would need sys-path import games (no `__init__.py` under tests/) for ~60 lines of code. Folded into conftest. |
| Waiting | `page.wait_for_function` / `wait_for_selector` with explicit `timeout=WAIT_MS` (10 000 ms) everywhere; zero `time.sleep` for state (the only sleep is the 0.1 s interval inside the HTTP readiness poll loop, which polls a condition) |

Two determinism keystones, used by every test:

1. **WS-open gate.** Any context that must observe a live update waits
   `window.__plannerDebug.wsOpens >= 1` **before** the triggering mutation fires. Built into
   `open_page(...)` so it cannot be forgotten.
2. **Catch-up settle.** A fresh WS connects with `since=0`; if the test wrote events *before*
   opening the page (create/propose setup), the server replays them as one batch → exactly one
   deferred flush → `route()` **replaces the screen DOM** ~debounce later. Interacting before
   that flush lands is the flake: a filled textarea or located element gets wiped/detached.
   Rule: when events precede page-open, `open_page` additionally waits
   `__plannerDebug.flushes >= 1`, then re-waits the ready selector. After that flush the DOM is
   quiescent until the test's own next mutation (non-empty batches only + no background loops).

---

## A. `tests/e2e/conftest.py` — file blueprint

Standalone: imports nothing from `tests/unit`. Imports: `json, os, socket, subprocess, time,
dataclasses.dataclass, pathlib.Path, types.SimpleNamespace, collections.abc.{Callable,Iterator}`,
`httpx`, `pytest`, `playwright.sync_api.{Browser, BrowserContext, Page}`.

### Module constants

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "plan"
FAKE_NOW = "2026-07-04T12:00:00"
WAIT_MS = 10_000          # every Playwright wait
BOOT_BUDGET_S = 15.0      # server readiness budget
```

### `ServerHandle` (frozen dataclass)

`base: str` (e.g. `http://127.0.0.1:61234`), `proc: subprocess.Popen`, `db_path: Path`,
`log_path: Path`.

### `server_factory` fixture (function scope)

Yields `make(gateway: str | None = None) -> ServerHandle`; finalizer tears down every server
it booted. Per call (counter `n` for subdirs `tmp_path / f"srv{n}"`):

1. `port = _free_port()` — bind `("127.0.0.1", 0)`, read the assigned port, close.
2. Env: `env = {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}` (keeps
   PATH/HOME, strips ambient `PLAN_*` leakage), then set:
   - `PLAN_TEST_MODE=1`
   - `PLAN_DB_PATH=<srvdir>/planning.db`
   - `PLAN_PORT=<port>`
   - `PLAN_FAKE_NOW=2026-07-04T12:00:00`
   - `PLAN_LOGS_DIR=<srvdir>/logs`
   - `PLAN_DISPATCHER_LOCK_PATH=<srvdir>/dispatcher.lock`
   - `PLAN_WS_POLL_MS=50`
   - `PLAN_UI_DEBOUNCE_MS=50`
   - `PLAN_GATEWAY_ADAPTER=offline` only when `gateway == "offline"` (otherwise unset: `auto`
     resolves to the echo fake under test mode; spawn/boundary `auto` → fakes likewise).
   `config.yaml` at repo root is read by `plan serve` but every value the tests depend on is
   env-pinned (env wins, config.py); `dispatch_enabled: true` in the yaml is inert because test
   mode never starts loops.
3. Boot: open `<srvdir>/server.log` for write;
   `subprocess.Popen([str(PLAN_BIN), "serve"], cwd=REPO_ROOT, env=env, stdout=log, stderr=STDOUT)`.
   `cwd=REPO_ROOT` is load-bearing twice: the assets mount and the config.yaml path.
   (`plan serve` creates the schema itself — the fixture never touches the DB.)
4. Readiness: poll `httpx.get(f"{base}/api/meta", timeout=1.0)` every 0.1 s within
   `BOOT_BUDGET_S`. On 200: assert `meta["test_mode"] is True` and
   `meta["ui_debounce_ms"] == 50` (env plumbing proof), and assert
   `httpx.get(f"{base}/assets/api.js").status_code == 200` (catches a wrong-CWD mount loudly).
   Also fail fast inside the loop if `proc.poll() is not None` (crashed boot). On timeout/crash:
   terminate, raise `AssertionError` embedding the last ~40 log lines.
5. Teardown (finalizer, per server): `proc.terminate()`; `proc.wait(timeout=5)`; on
   `TimeoutExpired` → `proc.kill(); proc.wait()`. tmp dirs are pytest-managed.

### `server` fixture (function scope)

`return server_factory()` — the default echo-gateway instance every test uses. Test 26 calls
`server_factory(gateway="offline")` itself for its second instance.

### `context_factory` fixture (function scope)

Depends on pytest-playwright's session `browser`. Keeps `contexts: list[BrowserContext]`;
`make() -> BrowserContext` appends `browser.new_context()`; teardown closes each. No
permissions, no viewport overrides needed (no clipboard use in these six tests).

### `open_page` fixture (function scope, returns a callable)

```python
def open_page(ctx: BrowserContext, server: ServerHandle, route: str,
              ready_selector: str, settled: bool) -> Page:
```

1. `page = ctx.new_page()`; `page.goto(server.base + "/" + route)` (route like `#/board`,
   `#/ticket/<id>`, `#/review`).
2. `page.wait_for_selector(ready_selector, timeout=WAIT_MS)`.
3. WS-open gate: `page.wait_for_function("() => window.__plannerDebug && window.__plannerDebug.wsOpens >= 1", timeout=WAIT_MS)`.
4. If `settled` (the test wrote events before opening): wait
   `"() => window.__plannerDebug.flushes >= 1"`, then `wait_for_selector(ready_selector)` again
   (the flush re-render replaced the screen div).
5. Return the page.

`settled=True` for every page opened after setup mutations (tests 23–27); `settled=False` only
in test 22, whose pages open against a zero-event DB.

### `cli` fixture (function scope, returns a callable)

```python
def cli(server: ServerHandle, *args: str, ticket_id: str | None = None,
        actor: str | None = None, run_id: str | None = None, claim: str | None = None,
        stdin: str | None = None) -> dict:
```

- Env: strip ambient `PLAN_*` from `os.environ.copy()`, set `PLAN_SERVER_URL=server.base`,
  plus `PLAN_TICKET_ID`/`PLAN_ACTOR`/`PLAN_RUN_ID`/`PLAN_CLAIM` when given. (Default actor is
  the CLI's own `"agent"` — proposals through `cli` are the agent path by construction.)
- `subprocess.run([str(PLAN_BIN), *args, "--json"], input=stdin, capture_output=True,
  text=True, cwd=REPO_ROOT, env=env, timeout=30)`.
- `assert proc.returncode == 0, f"plan {' '.join(args)} rc={proc.returncode}\nstderr: {proc.stderr}"`.
- Return `json.loads(proc.stdout)`.

### `api` fixture (function scope)

`SimpleNamespace` of two thin httpx wrappers (10 s timeout, assert status < 300 with body in
the failure message):

- `get(server, path) -> dict` — no headers.
- `human_post(server, path, json_body) -> dict` — **no** `X-Plan-*` headers: `authctx`
  classifies a header-less request as the human, which is what `/grant`, `/accept`, `/approve`
  require. (Agent-path writes go through `cli`; no agent HTTP helper is needed by these tests.)

That is the whole conftest: 5 fixtures + 2 dataclass/helpers, ~180 lines.

---

## B. `tests/e2e/test_flows_a.py` — the six tests

Module docstring names SPEC §18.3 items 22–27 and the one-test-per-anchor fence. Exactly six
test functions exist; **no parametrize** on any of them (parametrized junit ids would count as
multiple anchored matches and FAIL the item); private helpers, if any, are module-level
non-`test_` functions. Pinned bodies are module constants.

```python
MD_BODY = (
    "# Success criteria\n"
    "\n"
    "Ship the T18 harness with **exact** assertions.\n"
    "\n"
    "- six anchored tests\n"
    "- two browser contexts\n"
    "\n"
    "1. boot server\n"
    "2. drive UI\n"
    "\n"
    "Done when verify flips items 22-27."
)
E24_BODY = "Agent-drafted success criteria."
E25_ORIG = "Original proposal body."
E25_EDIT = "Human-edited success criteria. "   # trailing space: stored verbatim, asserted verbatim
E27_SUCCESS = "Success body for the chain."
E27_APPROACH = "Approach body for the chain."
E27_PLAN = "Plan body for the chain."
```

### test_e22_cli_create_live_board  (item 22)

Setup: none — the DB must hold zero events at page-open so no catch-up flush exists and the
first flush is provably the create's.

1. `ctx_a, ctx_b = context_factory(), context_factory()`;
   `page_a = open_page(ctx_a, server, "#/board", 'section[data-screen="board"]', settled=False)`;
   `page_b = open_page(ctx_b, server, ...same..., settled=False)` — the "second context", on
   `#/board` with WS open **before** the create fires.
2. Baseline on both pages: `[data-column="needs_success"] [data-card]` count == 0; record
   `flushes_b = page_b.evaluate("window.__plannerDebug.flushes")` (expected 0).
3. `created = cli(server, "ticket", "create", "--title", "T18 board ticket")`;
   `tid = created["id"]`; assert `created["state"] == "needs_success"` (CLI JSON is the
   ticket payload).
4. **No reload, no goto** from here on. On `page_b`:
   `wait_for_function` that `document.querySelector('[data-column="needs_success"] [data-card][data-ticket-id="<tid>"]')`
   is non-null (arg-bound tid, `timeout=WAIT_MS`).
5. Assert the card's inner text contains `"T18 board ticket"`; assert
   `page_b.evaluate("window.__plannerDebug.flushes") > flushes_b` (the update arrived via a WS
   flush, not any navigation). Same card wait + text assert on `page_a` (both contexts live).

### test_e23_env_pinned_propose  (item 23)

1. `tid = cli(server, "ticket", "create", "--title", "T18 propose ticket")["id"]`.
2. `cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=MD_BODY)` —
   PLAN_TICKET_ID resolves the ticket (no positional id), stdin carries the body. R2 grant
   (`ceiling=needs_success, at_cap=propose`) makes the advance target exceed the ceiling, so
   the proposal parks pending on the success slot.
3. `page = open_page(ctx, server, f"#/ticket/{tid}", f'section[data-screen="ticket"][data-ticket-id="{tid}"]', settled=True)`.
4. Assertions ("rendered and intact", pinned precisely):
   - **Intact (verbatim source)**: `page.input_value('[data-field="success"] [data-edit]') == MD_BODY`
     — exact string equality; the textarea carries the proposal body byte-for-byte.
   - **Rendered (markdown structure)** under
     `B = '[data-field="success"] .proposal-card .markdown-block'`:
     - `inner_text(f"{B} h1") == "Success criteria"`
     - `eval_on_selector_all(f"{B} ul li", "els => els.map(e => e.textContent)") ==
       ["six anchored tests", "two browser contexts"]`
     - `eval_on_selector_all(f"{B} ol li", ...) == ["boot server", "drive UI"]`
     - `inner_text(f"{B} strong") == "exact"`
     - `f"{B} p"` count == 2 (intro + closing), closing p text ==
       `"Done when verify flips items 22-27."`
   - Ticket did not advance: the section still has `data-state="needs_success"`, and the
     field's *value* block is still empty (`[data-field="success"] .quiet-line` present —
     the proposal is shown as a proposal, not as the accepted value).

### test_e24_accept_in_review  (item 24)

1. `tid = cli(... "ticket", "create", "--title", "T18 review ticket")["id"]`;
   `cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=E24_BODY)`.
2. `page_a = open_page(ctx_a, server, "#/review", f'[data-review-card][data-entity-id="{tid}"]', settled=True)`;
   assert card attrs `data-kind == "success"`.
3. Second context watches the ticket page:
   `page_b = open_page(ctx_b, server, f"#/ticket/{tid}", 'section[data-screen="ticket"][data-state="needs_success"]', settled=True)`.
4. Accept-impossible ladder on `page_a`, `card = f'[data-review-card][data-entity-id="{tid}"]'`:
   - nothing picked → `page_a.is_disabled(f"{card} [data-accept]")` is True;
   - one half only: `select_option(f"{card} [data-grant-ceiling]", "none")` ("No further" is
     the option with value `"none"`) → still disabled;
   - other half: `check(f'{card} [data-grant-atcap] input[value="stop"]')` → wait_for_function
     until `[data-accept]` is enabled (both halves picked).
5. `click(f"{card} [data-accept]")`.
6. Queue departure: `page_a.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)` (this
   was the only queue entry on a fresh DB); API: `api.get(server, "/api/queues")["approvals"] == []`.
7. Second context updates **without reload**: on `page_b` (no reload/goto ever issued),
   `wait_for_function` until `section[data-screen="ticket"]` has
   `data-state == "needs_approach"` — the flip can only arrive via the WS flush re-render.
8. Exactly one state, grant per SPEC, via API `d = api.get(server, f"/api/tickets/{tid}")`:
   - `d["state"] == "needs_approach"` (needs_success → needs_approach, exactly one step);
   - `d["ceiling"] == "needs_approach"` — "no further" pins the ceiling to the new state;
   - `d["at_cap"] == "stop"`;
   - `d["fields"]["success"]["value"] == E24_BODY` and
     `d["fields"]["success"]["proposal"] is None`.

### test_e25_edit_accept_in_review  (item 25)

1. `tid = cli(... create ... "T18 edit ticket")["id"]`;
   `cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=E25_ORIG)`.
2. `page = open_page(ctx, server, "#/review", f'[data-review-card][data-entity-id="{tid}"]', settled=True)`.
3. **Prefill first**: `page.input_value(f"{card} [data-edit]") == E25_ORIG` (exact equality,
   asserted before any fill).
4. `fill(f"{card} [data-edit]", E25_EDIT)`; grant pair:
   `select_option(f"{card} [data-grant-ceiling]", "needs_plan")`,
   `check(f'{card} [data-grant-atcap] input[value="propose"]')`; wait `[data-accept]` enabled;
   click it. (The card sends `edited_body` because the textarea differs from the fetched body.)
5. `wait_for_selector("[data-review-empty]")`.
6. API `d = api.get(...)`:
   - `d["fields"]["success"]["value"] == E25_EDIT` — **exact**, trailing space included
     (failure message shows `repr`);
   - `d["ceiling"] == "needs_plan"`, `d["at_cap"] == "propose"`, `d["state"] == "needs_approach"`.
7. Renders on Ticket: navigate a fresh page
   `open_page(ctx, server, f"#/ticket/{tid}", 'section[data-screen="ticket"][data-state="needs_approach"]', settled=True)`;
   assert `inner_text('[data-field="success"] .markdown-block')` contains
   `"Human-edited success criteria."` (value equality is the API assert above; the DOM assert
   proves it renders as the field value).

### test_e26_chat_panel_echo_and_offline  (item 26)

Echo half (default `server`):

1. `tid = cli(... create ... "T18 chat ticket")["id"]`.
2. `page = open_page(ctx, server, f"#/ticket/{tid}", 'section[data-screen="ticket"] [data-chat] [data-chat-input]', settled=True)`.
3. `fill('[data-chat] [data-chat-input]', "hello from e2e")`; `click('[data-chat] [data-chat-send]')`.
4. `wait_for_selector('[data-chat] [data-chat-msg="you"]')`, text `== "hello from e2e"`;
   `wait_for_function` scanning `[data-chat] [data-chat-msg="planner"]` nodes for one whose
   text contains exactly `"echo: hello from e2e"` (fake shape `"echo: <text>"`, fakes.py:99).
   Document-level scan survives the `chat_session_created` flush re-render.
5. Persisted key via API: `api.get(server, f"/api/tickets/{tid}")["chat_session_key"] ==
   "fake-sess-1"` — non-null and exactly the first minted key (this test's server is fresh and
   this is its first chat send).

Offline half (boot-time adapter → second instance):

6. `off = server_factory(gateway="offline")`;
   `tid2 = cli(off, "ticket", "create", "--title", "T18 offline ticket")["id"]`.
7. `page2 = open_page(ctx2, off, f"#/ticket/{tid2}", 'section[data-screen="ticket"] [data-chat] [data-chat-offline]', settled=True)`.
8. Assert `[data-chat] [data-chat-offline]` present, and **no** `[data-chat-send]` / no
   `[data-chat-input]` anywhere in `[data-chat]` (the panel renders the notice instead of an
   input source when `status.available` is false).

### test_e27_auto_accept_chain  (item 27)

**Interpretation, stated for the reviewer.** SPEC item 27 says "two CLI proposals advance it to
`needs_plan` with the plan proposal pending". Two proposals alone cannot leave a plan proposal
pending: with `ceiling=needs_plan / at_cap=propose`, success and approach auto-accept (each
advance-target ≤ ceiling, `machine.auto_accept_target`), landing the ticket at `needs_plan`; a
*pending* plan proposal requires a **third** CLI proposal, which files and parks (at the ceiling
with `at_cap=propose`). This matches unit item 3 (§18.3 item 3) exactly: success, approach AND
plan are filed; the plan one stays pending. Reading: "two CLI proposals advance it" (success,
approach) + the plan proposal (also via CLI) parks. The test drives three `plan propose` calls.

1. `tid = cli(server, "ticket", "create", "--title", "T18 chain ticket")["id"]`.
2. Human grant (API, no headers — `/grant` rejects agents):
   `g = api.human_post(server, f"/api/tickets/{tid}/grant", {"ceiling": "needs_plan", "at_cap": "propose"})`;
   assert `g["ceiling"] == "needs_plan"`, `g["at_cap"] == "propose"`.
3. Chain via CLI (each returns the ticket JSON — assert the intermediate states):
   - `r1 = cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=E27_SUCCESS)`
     → `r1["state"] == "needs_approach"` (auto-accepted + advanced);
   - `r2 = cli(..., "approach", ...)` → `r2["state"] == "needs_plan"`;
   - `r3 = cli(..., "plan", ...)` → `r3["state"] == "needs_plan"` (parked, no advance) and
     `r3["fields"]["plan"]["proposal"]["body"] == E27_PLAN`.
4. End state via API `d = api.get(server, f"/api/tickets/{tid}")`:
   - `d["state"] == "needs_plan"`, `d["ceiling"] == "needs_plan"`, `d["at_cap"] == "propose"`;
   - `d["fields"]["success"]["value"] == E27_SUCCESS`,
     `d["fields"]["approach"]["value"] == E27_APPROACH` (auto-accepts stored as values);
   - `d["fields"]["plan"]["value"] is None` and `d["fields"]["plan"]["proposal"]` non-null.
5. Approval queue: `q = api.get(server, "/api/queues")["approvals"]`; assert exactly one entry
   and `q[0]["entity_id"] == tid`, `q[0]["kind"] == "plan"`.
6. UI proof (this is the e2e suite): `open_page(ctx, server, "#/review",
   f'[data-review-card][data-entity-id="{tid}"]', settled=True)`; assert the card's
   `data-kind == "plan"`.

---

## C. helpers.py — not created

All shared machinery is fixture-shaped (server boot, contexts, `open_page`, `cli`, `api`) and
lives in `conftest.py`, injected by pytest. A separate `helpers.py` would need the implicit
sys-path insertion trick (`tests/e2e` has no `__init__.py`) and a collision-prone module name,
for no structural gain at this size. If T19+ grows the harness past ~250 lines, extraction is a
follow-up ticket's call.

---

## D. Risk register — flake surfaces and countermeasures

| # | Flake surface | Countermeasure |
|---|---|---|
| 1 | WS-open race: mutation fires before a context's socket is connected → invalidation missed → live-update wait times out | `open_page` always gates on `wsOpens >= 1` before returning; every mutation that must be observed live fires only after `open_page` returned for every watching context |
| 2 | Catch-up flush race: `since=0` replays pre-open setup events → one deferred re-render replaces the screen DOM mid-interaction (wipes a filled textarea, detaches located nodes) | the `settled` step in `open_page`: when events precede page-open, wait `flushes >= 1` and re-wait the ready selector; afterwards the DOM is quiescent (non-empty batches only, no background loops in test mode) until the test's own next mutation |
| 3 | Subprocess boot latency / silent crash | readiness poll on `/api/meta` (0.1 s interval, 15 s budget) with `proc.poll()` crash check inside the loop; failure raises with the server-log tail; `/assets/api.js` probe catches a wrong-CWD static mount at boot rather than as a blank-page timeout later |
| 4 | Port collisions | OS-assigned ephemeral port per server (`bind(("127.0.0.1", 0))`); per-test servers never reuse a port; the TOCTOU window is accepted — a collision surfaces as a loud readiness failure, not a silent misdirect |
| 5 | Cross-context flush timing (accept in context A, assert in context B) | condition-polling only: `wait_for_function`/`wait_for_selector` with `timeout=WAIT_MS` on document-level queries (never held element handles); 50 ms poll + 50 ms debounce keeps end-to-end latency ≪ budget |
| 6 | Frozen clock ties (`waiting_since`, `created_at` all equal) | fresh DB per test with at most one approval entry — no test asserts ordering among same-timestamp entities |
| 7 | Ambient env leakage (`PLAN_TICKET_ID` etc. set in the invoking shell) | both the server env and the CLI env strip all `PLAN_*` keys from `os.environ` before pinning their own |
| 8 | Scorer anchor fence | exactly six `test_` functions in the whole e2e suite, one per anchor; no parametrize; helpers are non-`test_` names. Skip-scan: no skip/xfail/`.only` tokens anywhere in the files (including comments — the scan is substring-based), no empty bodies, no commented-out tests |
| 9 | Hung wait wedging the suite | explicit `timeout=WAIT_MS` on every wait (no Playwright default-timeout reliance); CLI subprocess `timeout=30`; verify's own 1800 s e2e ceiling is the last resort |

**Gate before hand-off to integration:** `.venv/bin/pytest tests/e2e/test_flows_a.py -q` green
**twice consecutively**, headless, from a clean checkout state; then full `./verify` showing
items 22–27 PASS and no gate regressions.

---

## E. Test list

| Test name | Item | SPEC values asserted |
|---|---|---|
| `test_e22_cli_create_live_board` | 22 | CLI-created ticket appears as `[data-card][data-ticket-id]` inside `[data-column="needs_success"]` in a second context with zero reload/goto; `flushes` counter increments (WS path); CLI JSON `state == "needs_success"` |
| `test_e23_env_pinned_propose` | 23 | `PLAN_TICKET_ID` env + stdin body; `[data-edit]` value `== MD_BODY` byte-for-byte; rendered `h1/ul(2 li)/ol(2 li)/strong/p` structure with exact texts; state stays `needs_success`; value slot still empty |
| `test_e24_accept_in_review` | 24 | `[data-accept]` disabled with nothing picked and with ceiling-only picked; enabled with both; "No further"(=`"none"`) + `stop` → state `needs_success`→`needs_approach` (exactly one), API `ceiling == "needs_approach"`, `at_cap == "stop"`; approvals `== []`; `[data-review-empty]`; second context's `data-state` flips without reload |
| `test_e25_edit_accept_in_review` | 25 | `[data-edit]` prefilled `== E25_ORIG`; after edit-accept with (`needs_plan`, `propose`): API value `== E25_EDIT` exactly (trailing space), `ceiling == "needs_plan"`, `at_cap == "propose"`; edited text renders in the field's markdown block on Ticket |
| `test_e26_chat_panel_echo_and_offline` | 26 | `[data-chat-msg="you"]` == sent text; planner reply contains `"echo: hello from e2e"`; API `chat_session_key == "fake-sess-1"`; offline server instance renders `[data-chat-offline]` with no `[data-chat-send]`/`[data-chat-input]` |
| `test_e27_auto_accept_chain` | 27 | grant (`needs_plan`, `propose`); propose success → `needs_approach`, approach → `needs_plan`, plan → parks; API state/ceiling/at_cap `== needs_plan/needs_plan/propose`; success+approach stored as values, plan proposal pending; `/api/queues` approvals exactly `[(tid, "plan")]`; Review shows `[data-review-card][data-kind="plan"]` |

---

## F. Selector hooks — no asset changes needed

Every hook each test needs was verified present on disk: board
(`section[data-screen="board"]`, `[data-column]`, `[data-card][data-ticket-id]` —
screens-board.js), ticket (`section[data-screen="ticket"][data-ticket-id][data-state]`,
`[data-field]`, `[data-chat]` — screens-ticket.js), review (`[data-review-card][data-entity-id]
[data-kind]`, `[data-review-empty]` — screens-review.js), proposal card (`[data-accept]`,
`[data-edit]`, `[data-grant-ceiling]`, `[data-grant-atcap]` — components.js), chat
(`[data-chat-panel]`, `[data-chat-input]`, `[data-chat-send]`, `[data-chat-msg]`,
`[data-chat-offline]` — components.js), debug counters (`window.__plannerDebug` — api.js).
**The dedicated additive-data-attribute section is therefore empty: no asset file is touched.**

## H. Binding amendments (post codex plan review — see plan-review.md)

**A1 — create `srvdir` in the parent before opening the log.** `server_factory` step 3 opens
`<srvdir>/server.log` from the test process; `plan serve` only creates directories inside the
subprocess, later. Add `srvdir.mkdir(parents=True, exist_ok=True)` immediately after computing
`srvdir`, before the log-file open.

**A2 — test 26 echo half uses two sends (chat-reply repaint race).** components.js:673 skips
the reply `paint()` when a WS flush re-render detached the panel mid-flight, and
chat/service.py emits `chat_session_created` on the FIRST send only — so a first-send reply
can be lost to the re-render and nothing ever repaints it. Deterministic drive:

1. Record `f0 = page.evaluate("window.__plannerDebug.flushes")`; fill + send `"warmup"`.
2. Wait `flushes > f0` (the `chat_session_created` flush — guaranteed, the event is written
   before the send response), then re-wait `'[data-chat] [data-chat-input]'` (screen replaced).
3. API assert `chat_session_key == "fake-sess-1"` (persisted by the warmup send).
4. Fill + send `"hello from e2e"`. Subsequent sends emit NO event → no flush → the panel stays
   connected → the reply paint cannot be raced.
5. Wait: last `[data-chat-msg="you"]` text `== "hello from e2e"` (two "you" messages exist
   now); planner-message scan for text containing `"echo: hello from e2e"` unchanged.

The offline half is unchanged. Step 5's document-level scan and every other test are
unaffected by either amendment.

## G. Style and fences

- ruff clean at line-length 100 (rules E,F,W,I,UP,B); sorted imports; no unused names.
- No `time.sleep` for state; the readiness poll's 0.1 s interval is the only sleep and it polls
  a condition inside a budget.
- `tests/e2e/conftest.py` is independent of `tests/unit/conftest.py` (style reference only).
- mypy scope is `src/` — tests are not type-checked, but signatures above are annotated anyway.
- Any later change to these tests requires a logged justification in decisions.md (SPEC §18.3).
