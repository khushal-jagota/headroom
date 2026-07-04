# T02 impl-review — codex exec run by integration-check agent on 2026-07-04 11:49 BST

Reading additional input from stdin...
OpenAI Codex v0.142.5
--------
workdir: /Users/khushaljagota/.hermes/planning-v2
model: gpt-5.5
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019f2cbf-638f-7e73-825f-ac06152df76d
--------
user
Audit the verify instrument against its contract. Read SPEC.md lines 260-275 (section 18.2 and the fences), orchestration/tickets/T02-verify/ticket.md, orchestration/tickets/T02-verify/plan-review.md. Then inspect: verify, scripts/verify.py, scripts/verify_lib.py. Check concretely: gate order (ruff, mypy, unit, build check, e2e) with the skip-scan as preflight; all six forbidden patterns detected; anchored test_aNN_/test_eNN_ matching; scoreboard always prints all 36 items and ends with exactly 'VERIFY: N/36 PASS'; exit 0 only when scan clean + gates pass + N==36; missing junit -> suite items FAIL without crash; ITEMS registry matches SPEC 18.3 numbering (unit 1-21+36, e2e 22-35). Number findings with file/line. If faithful: 'NO VIOLATIONS' plus up to 3 suggestions.
deprecated: `[features].web_search_request` is deprecated because web search is enabled by default.
Set `web_search` to `"live"`, `"indexed"`, `"cached"`, or `"disabled"` at the top level (or under a profile) in config.toml if you want to override it.
warning: Skill descriptions were shortened to fit the 2% skills context budget. Codex can still see every skill, but some descriptions are shorter. Disable unused skills or plugins to leave more room for the rest.
codex
I’ll read the contract sections and the verify implementation first, then cross-check each requested invariant against exact line references.
exec
/bin/zsh -lc 'nl -ba orchestration/tickets/T02-verify/ticket.md' in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc "sed -n '260,275p' SPEC.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
### 18.2 The verify instrument

- One command: `./verify` (executable script at repo root). Runs in order: ruff, mypy, unit suite, build check (`python -m compileall src/` + `node --check` on every file in `assets/`), then the Playwright e2e suite headless. Prints a per-item scoreboard for every acceptance test below (item number, name, PASS/FAIL) and ends with exactly `VERIFY: N/36 PASS`.
- The instrument fails with an explicit message if any test file contains `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, a commented-out test, or an empty test body — the scan itself is unit-tested (item 21).
- Built at stage 2, before implementation; all items report FAIL until their stage lands. The scoreboard is the single source of truth for completeness.
- Unit items are 1–21 and 36; end-to-end items are 22–35. Item 34 (snapshot migration) runs against `migration/source-snapshot/`, and item 35 (dogfood Level A) is the scripted walkthrough — both part of the suite, not manual steps. Levels B and C of the dogfood (Section 18.5) are deliberately OUTSIDE `./verify`: they use live agents and belong to stage 7's evidence, not the deterministic gate.

### 18.3 Acceptance tests

**Fences:**
- Each item maps 1:1 to a named test whose name includes the item number (`test_a01_...`, `test_e22_...`).
- Assertions use the specific values in this document — exact states, exact orderings, exact error shapes — not weakened approximations.
- After first written, any change to a test requires a logged justification in decisions.md.
- A claim of passing is valid only if `./verify` ran fresh in the same turn with full output shown.

External boundaries (hermes spawn, boundary/replan agent, chat gateway) are faked in all tests. E2E uses a real server + temp SQLite DB + `PLAN_TEST_MODE=1`, driving ticks via the test endpoints and time via `PLAN_FAKE_NOW`. Multi-surface behavior uses two browser contexts.

 succeeded in 0ms:
     1	# T02 — The verify instrument (SPEC stage 2)
     2	
     3	## Scope
     4	
     5	`./verify` at repo root: the single source of truth for completeness, per SPEC §18.2. Built before any implementation; on the stage-1 tree it must run end to end and report all 36 items FAIL with `VERIFY: 0/36 PASS`, exiting non-zero, while the code gates (ruff, mypy, compileall, node --check) pass.
     6	
     7	This ticket text is also the implementation blueprint; the codex plan review runs against this file.
     8	
     9	## Deliverables
    10	
    11	1. `verify` — executable at repo root: `#!/bin/sh` exec of `.venv/bin/python scripts/verify.py "$@"`.
    12	2. `scripts/verify.py` — a preflight skip-scan of `tests/`, then the gates in the spec's stated order: (a) `ruff check .`; (b) mypy strict over `src/`; (c) unit suite `pytest tests/unit`; (d) build check: `python -m compileall -q src/` and `node --check` on every `*.js` file under `assets/`; (e) e2e suite `pytest tests/e2e` (Playwright, headless). A scan violation prints an explicit message naming file+pattern, skips running the suites, and still prints the full scoreboard (all 36 FAIL — a tainted suite has no valid results). Each gate's raw output is streamed. Then the scoreboard: one line per item `[PASS|FAIL] item NN — <label>`, then exactly `VERIFY: N/36 PASS` as the final line. Exit 0 iff the scan is clean, every gate passed, and N == 36. Gate failures never suppress the scoreboard. verify.py's header comment records the instrument's documented limitation: it proves the named tests ran and passed; assertion strength is enforced by the §18.3 fences and the audit.
    13	3. `scripts/verify_lib.py` — importable logic (unit-testable, no side effects):
    14	   - `ITEMS`: the registry of all 36 acceptance items: number, suite (`unit`/`e2e`), required test-name token (`test_a01`..`test_a21`, `test_a36` unit; `test_e22`..`test_e35` e2e), short label taken from SPEC §18.3.
    15	   - `scan_test_files(paths) -> list[Violation]`: detects `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out tests (comment lines containing `def test_`), and empty test bodies (AST: a `test_*` function whose body is only `pass`/`...`/docstring). Returns file+pattern per violation.
    16	   - `score(junit_unit_xml, junit_e2e_xml) -> list[ItemResult]`: an item PASSes iff exactly one collected test name is anchored to its token (name starts with `test_aNN_` / `test_eNN_`, underscore required) and that test passed; zero matches, multiple matches, failure, error, or skip all mean FAIL.
    17	4. Pytest invocations write junit XML to a temp/`data/`-ignored location for scoring; verify runs both suites even when the earlier suite fails (the scoreboard must always be complete), but records gate failure. A missing or unparseable junit file scores every item of that suite FAIL; verify never crashes on gate failure.
    18	5. Zero-test tolerance: pytest exit code 5 (no tests collected) is not a crash at stage 2; the affected items simply score FAIL.
    19	6. The scan implements all six §18.2 patterns (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out test, empty body); item 21's test will assert detection of the four its text lists plus the remaining two.
    20	
    21	## Constraints
    22	
    23	- No test files are created by this ticket (the scan and scorer are exercised for real by item 21 in stage 3).
    24	- `scripts/` is not under mypy strict (instrument, not product), but must pass ruff.
    25	- The scan must be genuinely wired into `./verify` — the audit checks this.
    26	- Timeouts: e2e pytest invocation gets a generous overall timeout (config constant at top of verify.py), so a hung server can never wedge verify forever.
    27	
    28	## Acceptance for integration
    29	
    30	On the integrated stage-1 tree: `./verify` streams gates a–f, prints 36 FAIL lines and `VERIFY: 0/36 PASS`, exits non-zero. Ruff/mypy/compileall/node gates green. A synthetic file containing `pytest.skip(` dropped into `tests/unit/` makes verify abort with the explicit skip-scan message (manually spot-checked, then removed).

exec
/bin/zsh -lc 'nl -ba scripts/verify.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The verify instrument — the single source of truth for completeness (SPEC 18.2).
     2	
     3	Runs a preflight skip-scan of tests/, then the code and test gates in the spec's
     4	stated order (ruff, mypy, unit suite, build check, e2e suite), streaming each
     5	gate's output live. Ends with a 36-item scoreboard and exactly one final line,
     6	``VERIFY: N/36 PASS``. Exit is 0 iff the scan is clean, every gate passed, and
     7	N == 36.
     8	
     9	Documented limitation: this instrument proves the named tests RAN and PASSED. It
    10	does not judge assertion strength — that is enforced by the SPEC 18.3 fences
    11	(exact states, orderings, and error shapes) and the independent codex audit, not
    12	here.
    13	"""
    14	
    15	from __future__ import annotations
    16	
    17	import subprocess
    18	import sys
    19	from dataclasses import dataclass
    20	from pathlib import Path
    21	
    22	from verify_lib import ITEMS, ItemResult, parse_junit, scan_test_files, score
    23	
    24	REPO_ROOT = Path(__file__).resolve().parent.parent
    25	VENV_BIN = REPO_ROOT / ".venv" / "bin"
    26	DATA_VERIFY = REPO_ROOT / "data" / "verify"
    27	
    28	# Generous ceiling so a hung server can never wedge verify forever.
    29	E2E_TIMEOUT_SECONDS = 1800
    30	
    31	
    32	@dataclass
    33	class GateResult:
    34	    name: str
    35	    ok: bool
    36	    detail: str = ""
    37	
    38	
    39	def _run(cmd: list[str], timeout: int | None = None) -> tuple[int, bool]:
    40	    """Run a gate command with inherited stdio (live streaming).
    41	
    42	    Returns (returncode, timed_out). Never raises: a missing command or a
    43	    timeout is reported as a failed gate, not a crash.
    44	    """
    45	    printable = " ".join(str(c) for c in cmd)
    46	    print(f"\n=== {printable} ===", flush=True)
    47	    try:
    48	        proc = subprocess.run(cmd, cwd=REPO_ROOT, timeout=timeout)
    49	        return proc.returncode, False
    50	    except subprocess.TimeoutExpired:
    51	        print(f"[verify] gate timed out after {timeout}s and was killed", flush=True)
    52	        return -1, True
    53	    except FileNotFoundError as exc:
    54	        print(f"[verify] gate command not found: {exc}", flush=True)
    55	        return -1, False
    56	
    57	
    58	def run_ruff() -> GateResult:
    59	    rc, _ = _run([str(VENV_BIN / "ruff"), "check", "."])
    60	    return GateResult("ruff", rc == 0)
    61	
    62	
    63	def run_mypy() -> GateResult:
    64	    rc, _ = _run([str(VENV_BIN / "mypy"), "src/"])
    65	    return GateResult("mypy", rc == 0)
    66	
    67	
    68	def run_pytest(target: str, junit: Path, name: str, timeout: int | None = None) -> GateResult:
    69	    """Run a pytest suite, writing junit XML for scoring.
    70	
    71	    A stale junit from a prior run is removed first so a crash or timeout that
    72	    writes nothing scores the suite's items FAIL rather than reusing old data.
    73	    Exit code 5 (no tests collected) is tolerated at stage 2: the gate is not
    74	    green, but it is not a crash either.
    75	    """
    76	    if junit.exists():
    77	        junit.unlink()
    78	    cmd = [str(VENV_BIN / "pytest"), target, f"--junitxml={junit}"]
    79	    rc, timed_out = _run(cmd, timeout=timeout)
    80	    if timed_out:
    81	        return GateResult(name, False, "timed out")
    82	    if rc == 5:
    83	        return GateResult(name, False, "no tests collected")
    84	    return GateResult(name, rc == 0)
    85	
    86	
    87	def run_build_check() -> GateResult:
    88	    """compileall over src/ plus node --check on every *.js under assets/."""
    89	    ok = True
    90	    rc, _ = _run([sys.executable, "-m", "compileall", "-q", "src/"])
    91	    if rc != 0:
    92	        ok = False
    93	    assets = REPO_ROOT / "assets"
    94	    js_files = sorted(assets.rglob("*.js")) if assets.is_dir() else []
    95	    for js in js_files:
    96	        rc_js, _ = _run(["node", "--check", str(js)])
    97	        if rc_js != 0:
    98	            ok = False
    99	    return GateResult("build check", ok)
   100	
   101	
   102	def print_scoreboard(results: list[ItemResult]) -> None:
   103	    for r in results:
   104	        tag = "PASS" if r.passed else "FAIL"
   105	        print(f"[{tag}] item {r.item.number:02d} — {r.item.label}")
   106	
   107	
   108	def main() -> int:
   109	    DATA_VERIFY.mkdir(parents=True, exist_ok=True)
   110	
   111	    # Preflight skip-scan. A tainted suite has no valid results, so on any
   112	    # violation we name each file+pattern, skip the suites, and still print the
   113	    # full all-FAIL scoreboard.
   114	    violations = scan_test_files([REPO_ROOT / "tests"])
   115	    if violations:
   116	        print("[verify] SKIP-SCAN FAILED — forbidden patterns in tests/:", flush=True)
   117	        for v in violations:
   118	            print(f"    {v.file}: {v.pattern}", flush=True)
   119	        print(
   120	            "[verify] test suites skipped; a tainted suite has no valid results.",
   121	            flush=True,
   122	        )
   123	        print_scoreboard([ItemResult(item, False) for item in ITEMS])
   124	        print("VERIFY: 0/36 PASS")
   125	        return 1
   126	
   127	    gates: list[GateResult] = []
   128	    gates.append(run_ruff())
   129	    gates.append(run_mypy())
   130	
   131	    unit_junit = DATA_VERIFY / "unit.xml"
   132	    gates.append(run_pytest("tests/unit", unit_junit, "unit suite"))
   133	
   134	    gates.append(run_build_check())
   135	
   136	    e2e_junit = DATA_VERIFY / "e2e.xml"
   137	    gates.append(
   138	        run_pytest("tests/e2e", e2e_junit, "e2e suite", timeout=E2E_TIMEOUT_SECONDS)
   139	    )
   140	
   141	    results = score(parse_junit(unit_junit), parse_junit(e2e_junit))
   142	
   143	    print()
   144	    for gate in gates:
   145	        status = "ok" if gate.ok else "FAILED"
   146	        suffix = f" ({gate.detail})" if gate.detail else ""
   147	        print(f"[verify] gate {gate.name}: {status}{suffix}")
   148	    print()
   149	
   150	    print_scoreboard(results)
   151	    passed = sum(1 for r in results if r.passed)
   152	    print(f"VERIFY: {passed}/36 PASS")
   153	
   154	    ok = all(gate.ok for gate in gates) and passed == len(ITEMS)
   155	    return 0 if ok else 1
   156	
   157	
   158	if __name__ == "__main__":
   159	    assert len(ITEMS) == 36, f"item registry must hold 36 items, has {len(ITEMS)}"
   160	    sys.exit(main())

exec
/bin/zsh -lc 'nl -ba verify' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	#!/bin/sh
     2	# Single source of truth for completeness (SPEC 18.2). Runs from the repo root
     3	# regardless of the caller's working directory.
     4	cd "$(dirname "$0")" || exit 1
     5	exec .venv/bin/python scripts/verify.py "$@"

exec
/bin/zsh -lc 'nl -ba orchestration/tickets/T02-verify/plan-review.md' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	# T02 plan review (codex) — dispositions
     2	
     3	Codex reviewed the T02 ticket-as-blueprint against SPEC §18.2/§18.3. Six findings; all addressed in the revised ticket:
     4	
     5	1. **Gate order** — accepted. Skip-scan is a preflight, then gates run in the spec's stated order: ruff, mypy, unit, build check, e2e.
     6	2. **Scoreboard suppressed on scan abort** — accepted. On a scan violation, verify prints the explicit message naming file+pattern AND still prints the full 36-item scoreboard (all FAIL — a tainted suite has no valid results) and `VERIFY: 0/36 PASS`.
     7	3. **Substring token matching** — accepted. Matching is anchored: a test satisfies item NN iff its name starts with `test_aNN_` / `test_eNN_` (underscore required after the token).
     8	4. **Vacuous passes** — refuted as instrument scope. The instrument proves the named tests ran and passed; assertion strength is enforced by the §18.3 fences and the codex audit, not by `./verify`. Recorded as a documented limitation in verify.py's header comment.
     9	5. **Item-21 coverage vs six patterns** — accepted for the scan (implements all six: skip mark, pytest.skip(, xfail, .only, commented-out test, empty body). The item-21 named test asserts at minimum the four patterns the item text lists, plus the remaining two — added assertions strengthen, never weaken.
    10	6. **Missing junit XML** — accepted. A missing/unparseable junit file scores every item of that suite FAIL; the scoreboard always prints; verify never crashes on gate failure.

exec
/bin/zsh -lc 'nl -ba scripts/verify_lib.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Pure logic for the verify instrument (SPEC 18.2): item registry, skip-scan,
     2	junit parsing, and scoring.
     3	
     4	No side effects and no printing — everything here is importable and unit-testable
     5	(item 21 exercises the scan and scorer against synthetic inputs). verify.py owns
     6	argv, subprocess, and output.
     7	"""
     8	
     9	from __future__ import annotations
    10	
    11	import ast
    12	from collections.abc import Iterable, Iterator
    13	from dataclasses import dataclass
    14	from pathlib import Path
    15	from typing import NamedTuple
    16	from xml.etree import ElementTree
    17	
    18	
    19	@dataclass(frozen=True)
    20	class Item:
    21	    """One acceptance item from SPEC 18.3."""
    22	
    23	    number: int
    24	    suite: str  # "unit" | "e2e"
    25	    token: str  # anchor prefix, e.g. "test_a01" / "test_e22"
    26	    label: str  # short phrase for the scoreboard
    27	
    28	
    29	@dataclass(frozen=True)
    30	class ItemResult:
    31	    item: Item
    32	    passed: bool
    33	
    34	
    35	class Violation(NamedTuple):
    36	    """A forbidden pattern found in a scanned test file."""
    37	
    38	    file: str
    39	    pattern: str
    40	
    41	
    42	# The 36 acceptance items, in item order (units 1-21 and 36, e2e 22-35). The
    43	# scoreboard is printed in this order; scoring picks each item's junit map by
    44	# its suite, not its position.
    45	ITEMS: list[Item] = [
    46	    Item(1, "unit", "test_a01", "planning-date math"),
    47	    Item(2, "unit", "test_a02", "gating chain"),
    48	    Item(3, "unit", "test_a03", "ceiling auto-accept"),
    49	    Item(4, "unit", "test_a04", "at-cap stop vs propose"),
    50	    Item(5, "unit", "test_a05", "one pending proposal per field"),
    51	    Item(6, "unit", "test_a06", "edit-accept"),
    52	    Item(7, "unit", "test_a07", "result routing"),
    53	    Item(8, "unit", "test_a08", "recap"),
    54	    Item(9, "unit", "test_a09", "blocking"),
    55	    Item(10, "unit", "test_a10", "sprint-item permissions"),
    56	    Item(11, "unit", "test_a11", "dispatch ordering"),
    57	    Item(12, "unit", "test_a12", "day-ticket removal"),
    58	    Item(13, "unit", "test_a13", "sprint assignment rules"),
    59	    Item(14, "unit", "test_a14", "claim CAS"),
    60	    Item(15, "unit", "test_a15", "TTL/reclaim"),
    61	    Item(16, "unit", "test_a16", "circuit breaker"),
    62	    Item(17, "unit", "test_a17", "day-plan tree"),
    63	    Item(18, "unit", "test_a18", "boundary job"),
    64	    Item(19, "unit", "test_a19", "seed fixtures"),
    65	    Item(20, "unit", "test_a20", "freeze rules"),
    66	    Item(21, "unit", "test_a21", "instrument integrity"),
    67	    Item(22, "e2e", "test_e22", "CLI create to live board"),
    68	    Item(23, "e2e", "test_e23", "env-pinned propose"),
    69	    Item(24, "e2e", "test_e24", "accept in Review"),
    70	    Item(25, "e2e", "test_e25", "edit-accept in Review"),
    71	    Item(26, "e2e", "test_e26", "chat panel"),
    72	    Item(27, "e2e", "test_e27", "auto-accept chain e2e"),
    73	    Item(28, "e2e", "test_e28", "day view"),
    74	    Item(29, "e2e", "test_e29", "invalidation"),
    75	    Item(30, "e2e", "test_e30", "dispatcher e2e"),
    76	    Item(31, "e2e", "test_e31", "refresh restores state"),
    77	    Item(32, "e2e", "test_e32", "sprint view live"),
    78	    Item(33, "e2e", "test_e33", "seed e2e"),
    79	    Item(34, "e2e", "test_e34", "snapshot migration e2e"),
    80	    Item(35, "e2e", "test_e35", "dogfood Level A"),
    81	    Item(36, "unit", "test_a36", "onward grant"),
    82	]
    83	
    84	
    85	# --- skip-scan -------------------------------------------------------------
    86	
    87	# Substring patterns forbidden anywhere in a test line (SPEC 18.2). Note that
    88	# "@pytest.mark.skip" also catches "@pytest.mark.skipif" and "xfail" catches
    89	# both the marker and pytest.xfail() — stricter, never weaker.
    90	_TEXT_PATTERNS: tuple[str, ...] = (
    91	    "@pytest.mark.skip",
    92	    "pytest.skip(",
    93	    "xfail",
    94	    ".only",
    95	)
    96	
    97	
    98	def _iter_py_files(paths: Iterable[Path | str]) -> Iterator[Path]:
    99	    """Yield every *.py file for the given paths; a path may be a file or a dir."""
   100	    for raw in paths:
   101	        p = Path(raw)
   102	        if p.is_dir():
   103	            yield from sorted(p.rglob("*.py"))
   104	        elif p.suffix == ".py" and p.is_file():
   105	            yield p
   106	
   107	
   108	def _is_empty_test_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
   109	    """True when a test function's body is only pass / ... / string literals."""
   110	    body = list(fn.body)
   111	    for stmt in body:
   112	        if isinstance(stmt, ast.Pass):
   113	            continue
   114	        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
   115	            # a bare docstring, "..." (Ellipsis), or string literal is a no-op
   116	            if stmt.value.value is Ellipsis or isinstance(stmt.value.value, str):
   117	                continue
   118	        return False
   119	    return True
   120	
   121	
   122	def scan_test_files(paths: Iterable[Path | str]) -> list[Violation]:
   123	    """Scan test files for the six forbidden patterns of SPEC 18.2.
   124	
   125	    Returns a deduplicated (file, pattern) list. Files that cannot be read are
   126	    skipped; a file that will not parse still gets its text patterns scanned.
   127	    """
   128	    violations: list[Violation] = []
   129	    seen: set[tuple[str, str]] = set()
   130	
   131	    def add(file_str: str, pattern: str) -> None:
   132	        key = (file_str, pattern)
   133	        if key not in seen:
   134	            seen.add(key)
   135	            violations.append(Violation(file_str, pattern))
   136	
   137	    for path in _iter_py_files(paths):
   138	        try:
   139	            text = path.read_text(encoding="utf-8", errors="replace")
   140	        except OSError:
   141	            continue
   142	        file_str = str(path)
   143	
   144	        for line in text.splitlines():
   145	            for pattern in _TEXT_PATTERNS:
   146	                if pattern in line:
   147	                    add(file_str, pattern)
   148	            stripped = line.strip()
   149	            if stripped.startswith("#") and "def test_" in stripped:
   150	                add(file_str, "commented-out test")
   151	
   152	        try:
   153	            tree = ast.parse(text)
   154	        except SyntaxError:
   155	            continue
   156	        for node in ast.walk(tree):
   157	            if (
   158	                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
   159	                and node.name.startswith("test_")
   160	                and _is_empty_test_body(node)
   161	            ):
   162	                add(file_str, "empty test body")
   163	
   164	    return violations
   165	
   166	
   167	# --- junit parsing + scoring ----------------------------------------------
   168	
   169	
   170	def _outcome(testcase: ElementTree.Element) -> str:
   171	    """Map a junit <testcase> to passed / failed / error / skipped."""
   172	    tags = {child.tag.lower() for child in testcase}
   173	    if "error" in tags:
   174	        return "error"
   175	    if "failure" in tags:
   176	        return "failed"
   177	    if "skipped" in tags:
   178	        return "skipped"
   179	    return "passed"
   180	
   181	
   182	def parse_junit(path: Path | str) -> dict[str, str]:
   183	    """Parse a junit XML file into {classname::name: outcome}.
   184	
   185	    A missing or unparseable file yields an empty map, so every item scored
   186	    against it FAILs. Keys are fully qualified so same-named tests in different
   187	    files stay distinct (and thus count as multiple matches).
   188	    """
   189	    p = Path(path)
   190	    if not p.exists():
   191	        return {}
   192	    try:
   193	        tree = ElementTree.parse(p)
   194	    except (ElementTree.ParseError, OSError):
   195	        return {}
   196	    results: dict[str, str] = {}
   197	    for testcase in tree.iter("testcase"):
   198	        name = testcase.get("name")
   199	        if not name:
   200	            continue
   201	        classname = testcase.get("classname", "")
   202	        results[f"{classname}::{name}"] = _outcome(testcase)
   203	    return results
   204	
   205	
   206	def score(
   207	    unit_results: dict[str, str], e2e_results: dict[str, str]
   208	) -> list[ItemResult]:
   209	    """Score every item. An item PASSes iff exactly one collected test name is
   210	    anchored to its token (bare name starts with ``<token>_``) and that test
   211	    passed; zero matches, multiple matches, failure, error, or skip mean FAIL.
   212	    """
   213	    out: list[ItemResult] = []
   214	    for item in ITEMS:
   215	        table = unit_results if item.suite == "unit" else e2e_results
   216	        prefix = item.token + "_"
   217	        matches = [
   218	            outcome
   219	            for key, outcome in table.items()
   220	            if key.rsplit("::", 1)[-1].startswith(prefix)
   221	        ]
   222	        passed = len(matches) == 1 and matches[0] == "passed"
   223	        out.append(ItemResult(item, passed))
   224	    return out

codex
The core shape already matches the requested flow at a glance. I’m pulling the full acceptance item list now so the registry check is against the actual SPEC numbering rather than just the summary sentence.
exec
/bin/zsh -lc "sed -n '276,380p' SPEC.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:

**Unit (pytest, pure logic + engine over a temp DB):**
1. Planning-date math: 2026-07-05T04:59 local → planning date 2026-07-04; T05:00 → 2026-07-05; boundary hour honored from config.
2. Gating chain: accepting success/approach/plan/result proposals advances exactly one state each, in order, with one `state_changed` event per step.
3. Ceiling auto-accept: ceiling `needs_plan` with `at_cap = propose`, proposals filed on success then approach auto-accept and advance; the plan proposal stays pending (state `needs_plan`, ticket in approval queue).
4. At-cap stop vs propose: at its ceiling with `at_cap = stop`, any agent proposal — including the current gating field — is rejected with the structured error; with `at_cap = propose`, a proposal on the current gating field files as pending and nothing advances; below the ceiling the behavior is identical for both values.
5. One pending proposal per field: a second proposal supersedes the first; `proposal_superseded` event carries the replaced body.
6. Edit-accept: accepting with edited text stores the edited text exactly as `value`, flags `edited: true` in the event.
7. Result routing: accepted result with ceiling `needs_review` → state `needs_review`; with ceiling `done` → `done`; approve action in `needs_review` → `done`.
8. Recap: write at `needs_success` rejected with structured error; write at `needs_approach` succeeds, overwrites prior recap, logs event, changes no state.
9. Blocking: ticket blocked by an open ticket is dispatch-ineligible; blocker → `done` makes it eligible; `blocks` cycle creation rejected.
10. Sprint-item permissions: agent `todo→active` succeeds; agent direct `active→done` write rejected; `done` via proposal + accept succeeds; `blocked_by` list stored and `blockers_cleared` computed when all blockers done.
11. Dispatch ordering: fixture of five eligible tickets orders P0 before P1; equal priority by earlier deadline; NULL deadline last; then `created_at`.
12. Day-ticket removal: deletes association only, position list re-packs contiguously, ticket state unchanged, `day_ticket_removed` logged.
13. Sprint assignment rules: standalone ticket accepts `sprint_id`; parented ticket's `sprint_id` write rejected; parent's sprint derived.
14. Claim CAS: two concurrent claim attempts on one ticket → exactly one wins, one `runs` row created.
15. TTL/reclaim: expired claim reclaimed (run `reclaimed`, lock cleared, eligible again); heartbeat extends expiry by one TTL.
16. Circuit breaker: two consecutive `crashed` runs → `auto_blocked`, ineligible; unblock action clears flag and counter; a `done` run resets the counter.
17. Day-plan tree: root invalidation marks root + all children invalidated and requests one replan; child invalidation replaces only that child; accept-all accepts every node and adds child tickets to the day list exactly once.
18. Boundary job (fake clock): first tick ≥ 05:00 creates the day, computes carryover (yesterday's non-done day tickets), overdue list, logs `day_closed`; second tick same date does nothing; explicit prior planning skips the judgment pass.
19. Seed fixtures: importing `tests/fixtures/planning-md/` yields the exact expected entity counts and spot-checked values (item statuses, ticket states per the Readiness mapping, preserved Chat ID, deferred items with NULL sprint, idea with project); re-running seeds zero duplicates.
20. Freeze rules: kickoff write after freeze rejected; `weekly_addenda` append still allowed; review same pattern. Sprint overlap rejected.
21. Instrument integrity: the skip-scan detects each forbidden pattern (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, empty body) in a synthetic file set and passes a clean set; `PLAN_FAKE_NOW` is ignored when `PLAN_TEST_MODE` is unset.

**End-to-end (Playwright, chromium, two browser contexts where stated):**
22. CLI create → live board: `plan ticket create` via subprocess with env pointing at the test server; the ticket appears on the Board in `needs_success` in a second context without reload.
23. Env-pinned propose: `plan propose success` with `PLAN_TICKET_ID` set and a multi-line markdown body on stdin; Ticket screen shows the proposal rendered, intact.
24. Accept in Review: the pending proposal appears on the Review screen; Accept is impossible without choosing the grant pair; choosing ("no further", stop) advances the ticket one state with ceiling equal to the new state and `at_cap = stop` (asserted via API); the item leaves the queue and both contexts update without reload.
25. Edit-accept in Review: the quick-edit textarea comes prefilled with the proposal; submitting altered text with grant (`needs_plan`, propose) stores the altered text exactly as the field value on Ticket, sets the ceiling to `needs_plan` and `at_cap` to propose.
26. Chat panel: opens on Ticket with the fake gateway (echo); a sent message renders a reply; `chat_session_key` persisted (asserted via API); gateway-offline fake renders the offline notice.
27. Auto-accept chain e2e: ticket with ceiling `needs_plan` and `at_cap = propose`: two CLI proposals advance it to `needs_plan` with the plan proposal pending in the approval queue.
28. Day view: with a fake boundary adapter, driving `POST /api/test/tick-boundary` at fake-now 05:01 produces the brief and a proposed tree; Accept-all adds the child tickets to the day list.
29. Invalidation: invalidating the root calls the fake replan adapter and renders the replacement tree; invalidating one child replaces only that child (other nodes keep status).
30. Dispatcher e2e: eligible ticket + fake spawn adapter; `POST /api/test/tick-dispatcher` claims it (run visible on Ticket); the fake worker's CLI calls (propose result with claim env) park it in `needs_review`; it appears in the approval queue; approve → `done`.
31. Refresh restores state: on Day, Board, and Ticket mid-flow (pending proposal, running claim), a reload renders identical content.
32. Sprint view live: `plan item set --status active` via CLI reflects on the Sprint screen in both contexts without reload; loose ticket appears in the loose section.
33. Seed e2e: `plan seed --source tests/fixtures/planning-md/` against the test server, then Board/Sprint/Backlog show the fixture's expected titles and counts.
34. Snapshot migration e2e: `plan seed --source migration/source-snapshot/` imports the frozen real data with exactly the ground truth stated in Section 12 (1 sprint with those dates; 12 items split 6/5/1 with "Ship waitlist mechanics." done; the 4 named tickets in their mapped states with the Chat ID preserved; 9 deferred items; 20 ideas); the migration report lists zero silently-skipped sections (anything unparsed is enumerated in it); a second run imports zero duplicates.
35. Dogfood Level A: `scripts/dogfood_cli.py` drives the full workflow against a test server seeded with `plan seed --demo` — agent actions through the real CLI, human actions (accepts with grant pairs, needs_review approve, day-plan decisions) through the HTTP API, since the CLI deliberately has no human verbs: create a ticket; propose and accept through every gate — every accept carrying its grant pair — including one edit-accept and one superseded proposal; write and overwrite the recap; assign to the day and remove; create a blocking link and confirm dispatch-ineligibility, then complete the blocker and confirm eligibility; claim via the test dispatcher tick; propose result with claim env; approve from needs_review to done. The script asserts each step's resulting state via `--json`/response output and exits non-zero on the first mismatch.
36. Onward grant (unit): a human accept missing either half of the grant pair is rejected with the structured error and changes nothing; accept with (`none`, `stop`) leaves the ticket resting — a subsequent agent proposal is rejected; accept with (`none`, `propose`) permits a pending proposal on the new gating field; accept with a later ceiling permits advancement to it; agent auto-accepts change neither ceiling nor at_cap; the `needs_review` approve action requires no grant.

### 18.5 Fake-ticket dogfood (stage 7)

The application must be proven with realistic fake tickets at three escalating levels of automation. This is the one sanctioned exception to the fakes-only rule: Levels B and C use the real local Hermes runtime — never inside `./verify`, always recorded in DOGFOOD.md with log evidence.

- **Level A — the CLI as a user and agent would drive it.** The scripted walkthrough of item 35. Deterministic, gating, part of `./verify`.
- **Level B — a Hermes agent works a fake ticket.** Spawn one real session: `hermes chat -q "Read <repo>/skills/planning-worker.md, then work planning ticket <id> using the plan CLI"` with the agent env vars pinned (server URL + ticket id) against a running server with a demo-seeded ticket shaped for it (ceiling `needs_plan`, `at_cap = propose`). Pass evidence, mechanical: at least one proposal and one recap write recorded on that ticket by the session (asserted from the event log), session output captured under `data/logs/` and referenced in DOGFOOD.md. The repo's skill artifacts are exercised via the read-this-file prompt because they are not installed into `~/.hermes/skills/` during the run.
- **Level C — a dispatched ticket agent end to end.** Enable the real spawn adapter (config), one eligible demo ticket, real dispatcher tick: claim → run row → the spawned agent writes at least one proposal carrying the claim env → run closes with a terminal outcome and the ticket lands where its ceiling dictates. Evidence: the run row, the event sequence, and the per-run log path, all in DOGFOOD.md.

Levels B and C depend on live agent behavior: apply the three-attempt rule per level (materially different approach each time — prompt shape, ticket shaping, ceiling), and record every attempt honestly in DOGFOOD.md. Completion requires both levels' mechanical evidence. If server, CLI, or dispatcher code changes after a level's evidence was produced, that level is stale and must be re-run.

### 18.4 Post-run human pass (not tests)

Live Hermes gateway chat; live dispatcher spawning a real agent; the final cutover seed against the live `~/.hermes/planning/` at switch time (the in-run migration proves the machinery on the frozen snapshot; the live directory will have drifted since 2026-07-04 and its import is reviewed by the human); installing skills into `~/.hermes/skills/`; launchd supervision; UI look-and-feel judgment.

exec
/bin/zsh -lc "sed -n '380,520p' SPEC.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
exec
/bin/zsh -lc 'rg -n "test_[ae][0-9]{2}|Acceptance tests|Item|dogfood|onward grant" SPEC.md' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
100:7. **The onward grant.** Human acceptance of a gating-field proposal (accept or edit-accept) requires the explicit grant pair: `next_ceiling` — any state at or beyond the newly entered state, or `none` meaning the ceiling becomes exactly the newly entered state — AND `at_cap` (`stop`: agents don't even propose from here; `propose`: agents may draft the next gating field for approval). Both are stated on every accept, even when the answer is "nothing further"; an accept without the pair is rejected with a structured error. Agent auto-accepts change neither ceiling nor at_cap. The `needs_review` approve action and sprint-item status accepts carry no onward grant (they are terminal for agent involvement).
201:4. **Ticket**: all four fields as sections (value rendered as markdown; pending proposal shown with Accept/Edit, accepts carrying the onward grant; notes editable inline), recap, state control, ceiling and at-cap as plain pickers (no dial widgetry), a **Copy ticket** button (copies a plain-text block — title, state, priority, field values, recap, links — to the clipboard for pasting into any agent or editor), links, day/sprint assignment, run history, event log, and the chat panel (Section 11).
213:`plan seed --source <dir>` reads a markdown planning directory with the current system's shapes and imports: `sprints/current/sprint-kickoff.md` → sprint kickoff fields; `sprint-tracking.md` items → sprint items (statuses: Todo→todo, In Progress→active, Done→done, Blocked→blocked, Deferred→deferred_next_sprint; `Priority:`/`Urgency:`/`Project:` fields mapped; Mode dropped); `sprint-review.md` → review fields; the latest `daily/YYYY-MM-DD/workspace.md` tickets → tickets (Readiness mapping: Concepts→needs_success, Needs Shaping→needs_approach, Ready→needs_plan, In Progress→in_progress; `Ticket ID:` preserved as an alias field; `Chat ID:` → `chat_session_key`; `Priority:` mapped; body/success/approach text into the matching fields' values) linked to sprint items by title match when unambiguous, else standalone with sprint assignment; `deferred.md` → sprint items with `sprint_id = NULL` (project sections and P-labels mapped); `ideas.md` → ideas. `plan seed --demo` creates a deterministic demo dataset for dogfooding and manual testing: 8 tickets spanning every state with varied priorities, deadlines, and ceilings; one blocking link; one parent sprint item with children; one planned day. It requires an empty database and errors otherwise.
249:**Build in this order**: (1) contracts skeleton — schema DDL, enums, typed models, adapter interfaces, config loading, CLI/API surface stubs; (2) **the verify instrument** (18.2), runnable with every checklist item FAIL; (3) pure-logic modules + unit tests (items 1–21 and 36) green; (4) server wiring — API, WS, resolution engine over DB, dispatcher, boundary scheduler, CLI against the API; (5) UI views; (6) Playwright e2e suite, items 22–34 green; (7) the fake-ticket dogfood pass (Section 18.5): Level A green (item 35), Levels B and C executed with evidence recorded in DOGFOOD.md. Do not start a later stage while an earlier stage's tests fail.
258:- **DOGFOOD.md** — the record of the Section 18.5 fake-ticket dogfood: per level, what was run, the exact commands, the evidence (event-log excerpts, run rows, session/log file paths under `data/logs/`), and an honest outcome. Narrative without corresponding logs is not evidence.
265:- Unit items are 1–21 and 36; end-to-end items are 22–35. Item 34 (snapshot migration) runs against `migration/source-snapshot/`, and item 35 (dogfood Level A) is the scripted walkthrough — both part of the suite, not manual steps. Levels B and C of the dogfood (Section 18.5) are deliberately OUTSIDE `./verify`: they use live agents and belong to stage 7's evidence, not the deterministic gate.
267:### 18.3 Acceptance tests
270:- Each item maps 1:1 to a named test whose name includes the item number (`test_a01_...`, `test_e22_...`).
314:35. Dogfood Level A: `scripts/dogfood_cli.py` drives the full workflow against a test server seeded with `plan seed --demo` — agent actions through the real CLI, human actions (accepts with grant pairs, needs_review approve, day-plan decisions) through the HTTP API, since the CLI deliberately has no human verbs: create a ticket; propose and accept through every gate — every accept carrying its grant pair — including one edit-accept and one superseded proposal; write and overwrite the recap; assign to the day and remove; create a blocking link and confirm dispatch-ineligibility, then complete the blocker and confirm eligibility; claim via the test dispatcher tick; propose result with claim env; approve from needs_review to done. The script asserts each step's resulting state via `--json`/response output and exits non-zero on the first mismatch.
317:### 18.5 Fake-ticket dogfood (stage 7)

exec
/bin/zsh -lc "rg -n \"test_a|test_e|@pytest.mark.skip|pytest.skip\\(|xfail|\\.only|commented-out|empty test body|VERIFY:|node --check|compileall|ruff|mypy|pytest tests\" -S ." in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
./CLAUDE.md:35:- Frontend is no-build vanilla JS in `assets/` — classic scripts, no bundler; syntax-check with `node --check`.
./orchestration/orchestrator-playbook.md:12:1. **Plan** — spawn a planning agent (Agent tool, subagent_type "general-purpose", model "opus"; use model "fable" only if your dispatch instructions say the ticket is hard) to write `orchestration/tickets/<TICKET>/plan.md`: file-by-file blueprint, function signatures, exact behaviors mapped to SPEC lines, test list with the named `test_aNN_`/`test_eNN_` tests and what each asserts.
./orchestration/orchestrator-playbook.md:15:4. **Implement** — spawn an implementation agent (model "opus") with plan.md as the blueprint. It must: keep to the ticket's owned files; pass `.venv/bin/ruff check .`, `.venv/bin/mypy src/`, and its named tests via `.venv/bin/pytest <its test files> -q`; never create skipped/empty tests; never weaken a fence.
./orchestration/orchestrator-playbook.md:20:- Tests are part of the ticket: the named tests must assert the SPEC's exact values (states, orderings, error shapes). No `pytest.skip`, no `xfail`, no empty bodies, no commented-out tests — the verify instrument scans for these and the run fails.
./pyproject.toml:28:[tool.ruff]
./pyproject.toml:33:[tool.ruff.lint]
./pyproject.toml:36:[tool.ruff.lint.per-file-ignores]
./pyproject.toml:40:[tool.mypy]
./pyproject.toml:43:mypy_path = "src"
./GOAL-CONDITION.md:15:1. `./verify` exits 0 and prints `VERIFY: 36/36 PASS` — the per-item scoreboard defined in SPEC.md Section 18.2, covering every acceptance test in Section 18.3, including the real-data snapshot migration (item 34) and the scripted CLI dogfood (item 35).
./orchestration/tickets/T08-instrument/ticket.md:5:One test file, `tests/unit/test_instrument.py`, containing `test_a21_instrument_integrity` (acceptance item 21):
./orchestration/tickets/T08-instrument/ticket.md:7:- The skip-scan (`scripts/verify_lib.scan_test_files`) detects each forbidden pattern in a synthetic file set written to tmp_path: `@pytest.mark.skip`, `pytest.skip(`, `xfail`, empty test body — plus `.only` and a commented-out test (the scan implements all six §18.2 patterns) — and passes a clean set.
./orchestration/tickets/T08-instrument/ticket.md:10:One named test per item; both halves live in `test_a21_...` since item 21 states both. Files owned: only `tests/unit/test_instrument.py`. ruff clean; test green.
./SPEC.md:20:- Tests: pytest for unit; Playwright (Python, chromium, headless) for end-to-end. Lint: ruff. Typecheck: mypy (strict on `src/`).
./SPEC.md:262:- One command: `./verify` (executable script at repo root). Runs in order: ruff, mypy, unit suite, build check (`python -m compileall src/` + `node --check` on every file in `assets/`), then the Playwright e2e suite headless. Prints a per-item scoreboard for every acceptance test below (item number, name, PASS/FAIL) and ends with exactly `VERIFY: N/36 PASS`.
./SPEC.md:263:- The instrument fails with an explicit message if any test file contains `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, a commented-out test, or an empty test body — the scan itself is unit-tested (item 21).
./SPEC.md:270:- Each item maps 1:1 to a named test whose name includes the item number (`test_a01_...`, `test_e22_...`).
./SPEC.md:298:21. Instrument integrity: the skip-scan detects each forbidden pattern (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, empty body) in a synthetic file set and passes a clean set; `PLAN_FAKE_NOW` is ignored when `PLAN_TEST_MODE` is unset.
./requirements.txt:16:mypy==2.1.0
./requirements.txt:17:mypy_extensions==1.1.0
./requirements.txt:32:ruff==0.15.20
./scripts/verify.py:4:stated order (ruff, mypy, unit suite, build check, e2e suite), streaming each
./scripts/verify.py:6:``VERIFY: N/36 PASS``. Exit is 0 iff the scan is clean, every gate passed, and
./scripts/verify.py:58:def run_ruff() -> GateResult:
./scripts/verify.py:59:    rc, _ = _run([str(VENV_BIN / "ruff"), "check", "."])
./scripts/verify.py:60:    return GateResult("ruff", rc == 0)
./scripts/verify.py:63:def run_mypy() -> GateResult:
./scripts/verify.py:64:    rc, _ = _run([str(VENV_BIN / "mypy"), "src/"])
./scripts/verify.py:65:    return GateResult("mypy", rc == 0)
./scripts/verify.py:88:    """compileall over src/ plus node --check on every *.js under assets/."""
./scripts/verify.py:90:    rc, _ = _run([sys.executable, "-m", "compileall", "-q", "src/"])
./scripts/verify.py:124:        print("VERIFY: 0/36 PASS")
./scripts/verify.py:128:    gates.append(run_ruff())
./scripts/verify.py:129:    gates.append(run_mypy())
./scripts/verify.py:152:    print(f"VERIFY: {passed}/36 PASS")
./orchestration/tickets/T02-verify/impl-review.md:16:Audit the verify instrument against its contract. Read SPEC.md lines 260-275 (section 18.2 and the fences), orchestration/tickets/T02-verify/ticket.md, orchestration/tickets/T02-verify/plan-review.md. Then inspect: verify, scripts/verify.py, scripts/verify_lib.py. Check concretely: gate order (ruff, mypy, unit, build check, e2e) with the skip-scan as preflight; all six forbidden patterns detected; anchored test_aNN_/test_eNN_ matching; scoreboard always prints all 36 items and ends with exactly 'VERIFY: N/36 PASS'; exit 0 only when scan clean + gates pass + N==36; missing junit -> suite items FAIL without crash; ITEMS registry matches SPEC 18.3 numbering (unit 1-21+36, e2e 22-35). Number findings with file/line. If faithful: 'NO VIOLATIONS' plus up to 3 suggestions.
./orchestration/tickets/T02-verify/impl-review.md:29:- One command: `./verify` (executable script at repo root). Runs in order: ruff, mypy, unit suite, build check (`python -m compileall src/` + `node --check` on every file in `assets/`), then the Playwright e2e suite headless. Prints a per-item scoreboard for every acceptance test below (item number, name, PASS/FAIL) and ends with exactly `VERIFY: N/36 PASS`.
./orchestration/tickets/T02-verify/impl-review.md:30:- The instrument fails with an explicit message if any test file contains `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, a commented-out test, or an empty test body — the scan itself is unit-tested (item 21).
./orchestration/tickets/T02-verify/impl-review.md:37:- Each item maps 1:1 to a named test whose name includes the item number (`test_a01_...`, `test_e22_...`).
./orchestration/tickets/T02-verify/impl-review.md:49:     5	`./verify` at repo root: the single source of truth for completeness, per SPEC §18.2. Built before any implementation; on the stage-1 tree it must run end to end and report all 36 items FAIL with `VERIFY: 0/36 PASS`, exiting non-zero, while the code gates (ruff, mypy, compileall, node --check) pass.
./orchestration/tickets/T02-verify/impl-review.md:56:    12	2. `scripts/verify.py` — a preflight skip-scan of `tests/`, then the gates in the spec's stated order: (a) `ruff check .`; (b) mypy strict over `src/`; (c) unit suite `pytest tests/unit`; (d) build check: `python -m compileall -q src/` and `node --check` on every `*.js` file under `assets/`; (e) e2e suite `pytest tests/e2e` (Playwright, headless). A scan violation prints an explicit message naming file+pattern, skips running the suites, and still prints the full scoreboard (all 36 FAIL — a tainted suite has no valid results). Each gate's raw output is streamed. Then the scoreboard: one line per item `[PASS|FAIL] item NN — <label>`, then exactly `VERIFY: N/36 PASS` as the final line. Exit 0 iff the scan is clean, every gate passed, and N == 36. Gate failures never suppress the scoreboard. verify.py's header comment records the instrument's documented limitation: it proves the named tests ran and passed; assertion strength is enforced by the §18.3 fences and the audit.
./orchestration/tickets/T02-verify/impl-review.md:58:    14	   - `ITEMS`: the registry of all 36 acceptance items: number, suite (`unit`/`e2e`), required test-name token (`test_a01`..`test_a21`, `test_a36` unit; `test_e22`..`test_e35` e2e), short label taken from SPEC §18.3.
./orchestration/tickets/T02-verify/impl-review.md:59:    15	   - `scan_test_files(paths) -> list[Violation]`: detects `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out tests (comment lines containing `def test_`), and empty test bodies (AST: a `test_*` function whose body is only `pass`/`...`/docstring). Returns file+pattern per violation.
./orchestration/tickets/T02-verify/impl-review.md:60:    16	   - `score(junit_unit_xml, junit_e2e_xml) -> list[ItemResult]`: an item PASSes iff exactly one collected test name is anchored to its token (name starts with `test_aNN_` / `test_eNN_`, underscore required) and that test passed; zero matches, multiple matches, failure, error, or skip all mean FAIL.
./orchestration/tickets/T02-verify/impl-review.md:63:    19	6. The scan implements all six §18.2 patterns (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out test, empty body); item 21's test will assert detection of the four its text lists plus the remaining two.
./orchestration/tickets/T02-verify/impl-review.md:68:    24	- `scripts/` is not under mypy strict (instrument, not product), but must pass ruff.
./orchestration/tickets/T02-verify/impl-review.md:74:    30	On the integrated stage-1 tree: `./verify` streams gates a–f, prints 36 FAIL lines and `VERIFY: 0/36 PASS`, exits non-zero. Ruff/mypy/compileall/node gates green. A synthetic file containing `pytest.skip(` dropped into `tests/unit/` makes verify abort with the explicit skip-scan message (manually spot-checked, then removed).
./orchestration/tickets/T02-verify/impl-review.md:82:     4	stated order (ruff, mypy, unit suite, build check, e2e suite), streaming each
./orchestration/tickets/T02-verify/impl-review.md:84:     6	``VERIFY: N/36 PASS``. Exit is 0 iff the scan is clean, every gate passed, and
./orchestration/tickets/T02-verify/impl-review.md:136:    58	def run_ruff() -> GateResult:
./orchestration/tickets/T02-verify/impl-review.md:137:    59	    rc, _ = _run([str(VENV_BIN / "ruff"), "check", "."])
./orchestration/tickets/T02-verify/impl-review.md:138:    60	    return GateResult("ruff", rc == 0)
./orchestration/tickets/T02-verify/impl-review.md:141:    63	def run_mypy() -> GateResult:
./orchestration/tickets/T02-verify/impl-review.md:142:    64	    rc, _ = _run([str(VENV_BIN / "mypy"), "src/"])
./orchestration/tickets/T02-verify/impl-review.md:143:    65	    return GateResult("mypy", rc == 0)
./orchestration/tickets/T02-verify/impl-review.md:166:    88	    """compileall over src/ plus node --check on every *.js under assets/."""
./orchestration/tickets/T02-verify/impl-review.md:168:    90	    rc, _ = _run([sys.executable, "-m", "compileall", "-q", "src/"])
./orchestration/tickets/T02-verify/impl-review.md:202:   124	        print("VERIFY: 0/36 PASS")
./orchestration/tickets/T02-verify/impl-review.md:206:   128	    gates.append(run_ruff())
./orchestration/tickets/T02-verify/impl-review.md:207:   129	    gates.append(run_mypy())
./orchestration/tickets/T02-verify/impl-review.md:230:   152	    print(f"VERIFY: {passed}/36 PASS")
./orchestration/tickets/T02-verify/impl-review.md:256:     5	1. **Gate order** — accepted. Skip-scan is a preflight, then gates run in the spec's stated order: ruff, mypy, unit, build check, e2e.
./orchestration/tickets/T02-verify/impl-review.md:257:     6	2. **Scoreboard suppressed on scan abort** — accepted. On a scan violation, verify prints the explicit message naming file+pattern AND still prints the full 36-item scoreboard (all FAIL — a tainted suite has no valid results) and `VERIFY: 0/36 PASS`.
./orchestration/tickets/T02-verify/impl-review.md:258:     7	3. **Substring token matching** — accepted. Matching is anchored: a test satisfies item NN iff its name starts with `test_aNN_` / `test_eNN_` (underscore required after the token).
./orchestration/tickets/T02-verify/impl-review.md:260:     9	5. **Item-21 coverage vs six patterns** — accepted for the scan (implements all six: skip mark, pytest.skip(, xfail, .only, commented-out test, empty body). The item-21 named test asserts at minimum the four patterns the item text lists, plus the remaining two — added assertions strengthen, never weaken.
./orchestration/tickets/T02-verify/impl-review.md:290:    25	    token: str  # anchor prefix, e.g. "test_a01" / "test_e22"
./orchestration/tickets/T02-verify/impl-review.md:311:    46	    Item(1, "unit", "test_a01", "planning-date math"),
./orchestration/tickets/T02-verify/impl-review.md:312:    47	    Item(2, "unit", "test_a02", "gating chain"),
./orchestration/tickets/T02-verify/impl-review.md:313:    48	    Item(3, "unit", "test_a03", "ceiling auto-accept"),
./orchestration/tickets/T02-verify/impl-review.md:314:    49	    Item(4, "unit", "test_a04", "at-cap stop vs propose"),
./orchestration/tickets/T02-verify/impl-review.md:315:    50	    Item(5, "unit", "test_a05", "one pending proposal per field"),
./orchestration/tickets/T02-verify/impl-review.md:316:    51	    Item(6, "unit", "test_a06", "edit-accept"),
./orchestration/tickets/T02-verify/impl-review.md:317:    52	    Item(7, "unit", "test_a07", "result routing"),
./orchestration/tickets/T02-verify/impl-review.md:318:    53	    Item(8, "unit", "test_a08", "recap"),
./orchestration/tickets/T02-verify/impl-review.md:319:    54	    Item(9, "unit", "test_a09", "blocking"),
./orchestration/tickets/T02-verify/impl-review.md:320:    55	    Item(10, "unit", "test_a10", "sprint-item permissions"),
./orchestration/tickets/T02-verify/impl-review.md:321:    56	    Item(11, "unit", "test_a11", "dispatch ordering"),
./orchestration/tickets/T02-verify/impl-review.md:322:    57	    Item(12, "unit", "test_a12", "day-ticket removal"),
./orchestration/tickets/T02-verify/impl-review.md:323:    58	    Item(13, "unit", "test_a13", "sprint assignment rules"),
./orchestration/tickets/T02-verify/impl-review.md:324:    59	    Item(14, "unit", "test_a14", "claim CAS"),
./orchestration/tickets/T02-verify/impl-review.md:325:    60	    Item(15, "unit", "test_a15", "TTL/reclaim"),
./orchestration/tickets/T02-verify/impl-review.md:326:    61	    Item(16, "unit", "test_a16", "circuit breaker"),
./orchestration/tickets/T02-verify/impl-review.md:327:    62	    Item(17, "unit", "test_a17", "day-plan tree"),
./orchestration/tickets/T02-verify/impl-review.md:328:    63	    Item(18, "unit", "test_a18", "boundary job"),
./orchestration/tickets/T02-verify/impl-review.md:329:    64	    Item(19, "unit", "test_a19", "seed fixtures"),
./orchestration/tickets/T02-verify/impl-review.md:330:    65	    Item(20, "unit", "test_a20", "freeze rules"),
./orchestration/tickets/T02-verify/impl-review.md:331:    66	    Item(21, "unit", "test_a21", "instrument integrity"),
./orchestration/tickets/T02-verify/impl-review.md:332:    67	    Item(22, "e2e", "test_e22", "CLI create to live board"),
./orchestration/tickets/T02-verify/impl-review.md:333:    68	    Item(23, "e2e", "test_e23", "env-pinned propose"),
./orchestration/tickets/T02-verify/impl-review.md:334:    69	    Item(24, "e2e", "test_e24", "accept in Review"),
./orchestration/tickets/T02-verify/impl-review.md:335:    70	    Item(25, "e2e", "test_e25", "edit-accept in Review"),
./orchestration/tickets/T02-verify/impl-review.md:336:    71	    Item(26, "e2e", "test_e26", "chat panel"),
./orchestration/tickets/T02-verify/impl-review.md:337:    72	    Item(27, "e2e", "test_e27", "auto-accept chain e2e"),
./orchestration/tickets/T02-verify/impl-review.md:338:    73	    Item(28, "e2e", "test_e28", "day view"),
./orchestration/tickets/T02-verify/impl-review.md:339:    74	    Item(29, "e2e", "test_e29", "invalidation"),
./orchestration/tickets/T02-verify/impl-review.md:340:    75	    Item(30, "e2e", "test_e30", "dispatcher e2e"),
./orchestration/tickets/T02-verify/impl-review.md:341:    76	    Item(31, "e2e", "test_e31", "refresh restores state"),
./orchestration/tickets/T02-verify/impl-review.md:342:    77	    Item(32, "e2e", "test_e32", "sprint view live"),
./orchestration/tickets/T02-verify/impl-review.md:343:    78	    Item(33, "e2e", "test_e33", "seed e2e"),
./orchestration/tickets/T02-verify/impl-review.md:344:    79	    Item(34, "e2e", "test_e34", "snapshot migration e2e"),
./orchestration/tickets/T02-verify/impl-review.md:345:    80	    Item(35, "e2e", "test_e35", "dogfood Level A"),
./orchestration/tickets/T02-verify/impl-review.md:346:    81	    Item(36, "unit", "test_a36", "onward grant"),
./orchestration/tickets/T02-verify/impl-review.md:353:    88	# "@pytest.mark.skip" also catches "@pytest.mark.skipif" and "xfail" catches
./orchestration/tickets/T02-verify/impl-review.md:354:    89	# both the marker and pytest.xfail() — stricter, never weaker.
./orchestration/tickets/T02-verify/impl-review.md:356:    91	    "@pytest.mark.skip",
./orchestration/tickets/T02-verify/impl-review.md:357:    92	    "pytest.skip(",
./orchestration/tickets/T02-verify/impl-review.md:358:    93	    "xfail",
./orchestration/tickets/T02-verify/impl-review.md:359:    94	    ".only",
./orchestration/tickets/T02-verify/impl-review.md:415:   150	                add(file_str, "commented-out test")
./orchestration/tickets/T02-verify/impl-review.md:427:   162	                add(file_str, "empty test body")
./orchestration/tickets/T02-verify/impl-review.md:518:21. Instrument integrity: the skip-scan detects each forbidden pattern (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, empty body) in a synthetic file set and passes a clean set; `PLAN_FAKE_NOW` is ignored when `PLAN_TEST_MODE` is unset.
./orchestration/tickets/T02-verify/impl-review.md:564:270:- Each item maps 1:1 to a named test whose name includes the item number (`test_a01_...`, `test_e22_...`).
./orchestration/tickets/T05-dispatch/ticket.md:5:The dispatch domain's pure logic and data layer plus the links infrastructure, and unit tests for acceptance items 9, 11, 14, 15, 16 (test names `test_a09_*`, `test_a11_*`, `test_a14_*`, `test_a15_*`, `test_a16_*`).
./orchestration/tickets/T05-dispatch/ticket.md:29:- ruff + mypy strict clean; all five named tests green.
./orchestration/tickets/T07-seed/ticket.md:5:The seed domain's parsing logic, import pipeline, migration report, and demo dataset, plus the synthetic fixture directory and the unit test for acceptance item 19 (`test_a19_*`). The CLI/API wiring of `plan seed` is stage 4; this ticket delivers a callable `seed_from_source(conn, source_dir) -> MigrationReport` and `seed_demo(conn) -> None` (errors if DB non-empty).
./orchestration/tickets/T07-seed/ticket.md:25:- ruff + mypy strict clean; the named test green.
./orchestration/tickets/T01-contracts/impl-review.md:127:1. `pyproject.toml` — package `planner` under `src/`, console script `plan = planner.cli.main:main`, ruff + mypy(strict on src/) + pytest configuration. `pip install -e .` into `.venv` must succeed.
./orchestration/tickets/T01-contracts/impl-review.md:138:- `ruff check .` and `mypy` (strict, src/) pass; `python -c "import planner"` and every submodule import succeed; `plan --help` exits 0.
./orchestration/tickets/T01-contracts/impl-review.md:143:ruff + mypy clean; editable install works; import smoke passes; CLI help renders the §8 verb tree; DDL creates all tables in a temp DB; schema columns match contracts.
./orchestration/tickets/T01-contracts/impl-review.md:168:src/planner/py.typed                  # mypy marker, ships with package
./orchestration/tickets/T01-contracts/impl-review.md:525:[tool.ruff]
./orchestration/tickets/T01-contracts/impl-review.md:530:[tool.ruff.lint]
./orchestration/tickets/T01-contracts/impl-review.md:533:[tool.mypy]
./orchestration/tickets/T01-contracts/impl-review.md:536:mypy_path = "src"
./orchestration/tickets/T01-contracts/impl-review.md:563:2. `ruff check .` clean; `mypy` (strict, src) clean; `python -c "import planner"` + import of every submodule succeeds (`python -m compileall src/`).
./orchestration/tickets/T01-contracts/impl-review.md:585:8. `assets/tokens.css` ships in T01: one `:root` block with every category from PRINCIPLES — surfaces, text tokens, accent (bright/surface/text), radius scale, motion durations (fast/base/slow), spacing scale, border widths — starter values, tuned in stage 5. `node --check` does not apply to CSS; no JS ships in T01.
./orchestration/tickets/T01-contracts/ticket.md:11:1. `pyproject.toml` — package `planner` under `src/`, console script `plan = planner.cli.main:main`, ruff + mypy(strict on src/) + pytest configuration. `pip install -e .` into `.venv` must succeed.
./orchestration/tickets/T01-contracts/ticket.md:22:- `ruff check .` and `mypy` (strict, src/) pass; `python -c "import planner"` and every submodule import succeed; `plan --help` exits 0.
./orchestration/tickets/T01-contracts/ticket.md:27:ruff + mypy clean; editable install works; import smoke passes; CLI help renders the §8 verb tree; DDL creates all tables in a temp DB; schema columns match contracts.
./orchestration/tickets/T04-tickets-engine/ticket.md:5:The tickets domain's pure logic and data layer: the state machine, the resolution engine (SPEC §4.4 — the single door to canonical values), proposal lifecycle, onward grant, recap rules, title limit, and ticket sprint-assignment rules. Plus the unit tests for acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 (exact assertions per SPEC §18.3, test names `test_a02_*` … anchored).
./orchestration/tickets/T04-tickets-engine/ticket.md:32:- ruff + mypy strict clean; all nine named tests green via `pytest tests/unit/test_tickets_engine.py`.
./orchestration/tickets/T03-days/ticket.md:5:The days domain's pure logic and data layer, plus unit tests for acceptance items 1, 12, 17, 18 (test names `test_a01_*`, `test_a12_*`, `test_a17_*`, `test_a18_*`).
./orchestration/tickets/T03-days/ticket.md:29:- ruff + mypy strict clean; all four named tests green.
./scripts/verify_lib.py:25:    token: str  # anchor prefix, e.g. "test_a01" / "test_e22"
./scripts/verify_lib.py:46:    Item(1, "unit", "test_a01", "planning-date math"),
./scripts/verify_lib.py:47:    Item(2, "unit", "test_a02", "gating chain"),
./scripts/verify_lib.py:48:    Item(3, "unit", "test_a03", "ceiling auto-accept"),
./scripts/verify_lib.py:49:    Item(4, "unit", "test_a04", "at-cap stop vs propose"),
./scripts/verify_lib.py:50:    Item(5, "unit", "test_a05", "one pending proposal per field"),
./scripts/verify_lib.py:51:    Item(6, "unit", "test_a06", "edit-accept"),
./scripts/verify_lib.py:52:    Item(7, "unit", "test_a07", "result routing"),
./scripts/verify_lib.py:53:    Item(8, "unit", "test_a08", "recap"),
./scripts/verify_lib.py:54:    Item(9, "unit", "test_a09", "blocking"),
./scripts/verify_lib.py:55:    Item(10, "unit", "test_a10", "sprint-item permissions"),
./scripts/verify_lib.py:56:    Item(11, "unit", "test_a11", "dispatch ordering"),
./scripts/verify_lib.py:57:    Item(12, "unit", "test_a12", "day-ticket removal"),
./scripts/verify_lib.py:58:    Item(13, "unit", "test_a13", "sprint assignment rules"),
./scripts/verify_lib.py:59:    Item(14, "unit", "test_a14", "claim CAS"),
./scripts/verify_lib.py:60:    Item(15, "unit", "test_a15", "TTL/reclaim"),
./scripts/verify_lib.py:61:    Item(16, "unit", "test_a16", "circuit breaker"),
./scripts/verify_lib.py:62:    Item(17, "unit", "test_a17", "day-plan tree"),
./scripts/verify_lib.py:63:    Item(18, "unit", "test_a18", "boundary job"),
./scripts/verify_lib.py:64:    Item(19, "unit", "test_a19", "seed fixtures"),
./scripts/verify_lib.py:65:    Item(20, "unit", "test_a20", "freeze rules"),
./scripts/verify_lib.py:66:    Item(21, "unit", "test_a21", "instrument integrity"),
./scripts/verify_lib.py:67:    Item(22, "e2e", "test_e22", "CLI create to live board"),
./scripts/verify_lib.py:68:    Item(23, "e2e", "test_e23", "env-pinned propose"),
./scripts/verify_lib.py:69:    Item(24, "e2e", "test_e24", "accept in Review"),
./scripts/verify_lib.py:70:    Item(25, "e2e", "test_e25", "edit-accept in Review"),
./scripts/verify_lib.py:71:    Item(26, "e2e", "test_e26", "chat panel"),
./scripts/verify_lib.py:72:    Item(27, "e2e", "test_e27", "auto-accept chain e2e"),
./scripts/verify_lib.py:73:    Item(28, "e2e", "test_e28", "day view"),
./scripts/verify_lib.py:74:    Item(29, "e2e", "test_e29", "invalidation"),
./scripts/verify_lib.py:75:    Item(30, "e2e", "test_e30", "dispatcher e2e"),
./scripts/verify_lib.py:76:    Item(31, "e2e", "test_e31", "refresh restores state"),
./scripts/verify_lib.py:77:    Item(32, "e2e", "test_e32", "sprint view live"),
./scripts/verify_lib.py:78:    Item(33, "e2e", "test_e33", "seed e2e"),
./scripts/verify_lib.py:79:    Item(34, "e2e", "test_e34", "snapshot migration e2e"),
./scripts/verify_lib.py:80:    Item(35, "e2e", "test_e35", "dogfood Level A"),
./scripts/verify_lib.py:81:    Item(36, "unit", "test_a36", "onward grant"),
./scripts/verify_lib.py:88:# "@pytest.mark.skip" also catches "@pytest.mark.skipif" and "xfail" catches
./scripts/verify_lib.py:89:# both the marker and pytest.xfail() — stricter, never weaker.
./scripts/verify_lib.py:91:    "@pytest.mark.skip",
./scripts/verify_lib.py:92:    "pytest.skip(",
./scripts/verify_lib.py:93:    "xfail",
./scripts/verify_lib.py:94:    ".only",
./scripts/verify_lib.py:150:                add(file_str, "commented-out test")
./scripts/verify_lib.py:162:                add(file_str, "empty test body")
./orchestration/tickets/T01-contracts/plan.md:21:src/planner/py.typed                  # mypy marker, ships with package
./orchestration/tickets/T01-contracts/plan.md:1170:[tool.ruff]
./orchestration/tickets/T01-contracts/plan.md:1175:[tool.ruff.lint]
./orchestration/tickets/T01-contracts/plan.md:1178:[tool.mypy]
./orchestration/tickets/T01-contracts/plan.md:1181:mypy_path = "src"
./orchestration/tickets/T01-contracts/plan.md:1208:2. `ruff check .` clean; `mypy` (strict, src) clean; `python -c "import planner"` + import of every submodule succeeds (`python -m compileall src/`).
./orchestration/tickets/T01-contracts/plan.md:1230:8. `assets/tokens.css` ships in T01: one `:root` block with every category from PRINCIPLES — surfaces, text tokens, accent (bright/surface/text), radius scale, motion durations (fast/base/slow), spacing scale, border widths — starter values, tuned in stage 5. `node --check` does not apply to CSS; no JS ships in T01.
./orchestration/tickets/T02-verify/ticket.md:5:`./verify` at repo root: the single source of truth for completeness, per SPEC §18.2. Built before any implementation; on the stage-1 tree it must run end to end and report all 36 items FAIL with `VERIFY: 0/36 PASS`, exiting non-zero, while the code gates (ruff, mypy, compileall, node --check) pass.
./orchestration/tickets/T02-verify/ticket.md:12:2. `scripts/verify.py` — a preflight skip-scan of `tests/`, then the gates in the spec's stated order: (a) `ruff check .`; (b) mypy strict over `src/`; (c) unit suite `pytest tests/unit`; (d) build check: `python -m compileall -q src/` and `node --check` on every `*.js` file under `assets/`; (e) e2e suite `pytest tests/e2e` (Playwright, headless). A scan violation prints an explicit message naming file+pattern, skips running the suites, and still prints the full scoreboard (all 36 FAIL — a tainted suite has no valid results). Each gate's raw output is streamed. Then the scoreboard: one line per item `[PASS|FAIL] item NN — <label>`, then exactly `VERIFY: N/36 PASS` as the final line. Exit 0 iff the scan is clean, every gate passed, and N == 36. Gate failures never suppress the scoreboard. verify.py's header comment records the instrument's documented limitation: it proves the named tests ran and passed; assertion strength is enforced by the §18.3 fences and the audit.
./orchestration/tickets/T02-verify/ticket.md:14:   - `ITEMS`: the registry of all 36 acceptance items: number, suite (`unit`/`e2e`), required test-name token (`test_a01`..`test_a21`, `test_a36` unit; `test_e22`..`test_e35` e2e), short label taken from SPEC §18.3.
./orchestration/tickets/T02-verify/ticket.md:15:   - `scan_test_files(paths) -> list[Violation]`: detects `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out tests (comment lines containing `def test_`), and empty test bodies (AST: a `test_*` function whose body is only `pass`/`...`/docstring). Returns file+pattern per violation.
./orchestration/tickets/T02-verify/ticket.md:16:   - `score(junit_unit_xml, junit_e2e_xml) -> list[ItemResult]`: an item PASSes iff exactly one collected test name is anchored to its token (name starts with `test_aNN_` / `test_eNN_`, underscore required) and that test passed; zero matches, multiple matches, failure, error, or skip all mean FAIL.
./orchestration/tickets/T02-verify/ticket.md:19:6. The scan implements all six §18.2 patterns (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out test, empty body); item 21's test will assert detection of the four its text lists plus the remaining two.
./orchestration/tickets/T02-verify/ticket.md:24:- `scripts/` is not under mypy strict (instrument, not product), but must pass ruff.
./orchestration/tickets/T02-verify/ticket.md:30:On the integrated stage-1 tree: `./verify` streams gates a–f, prints 36 FAIL lines and `VERIFY: 0/36 PASS`, exits non-zero. Ruff/mypy/compileall/node gates green. A synthetic file containing `pytest.skip(` dropped into `tests/unit/` makes verify abort with the explicit skip-scan message (manually spot-checked, then removed).
./orchestration/tickets/T06-sprints/ticket.md:5:The sprints domain's pure logic and data layer, plus unit tests for acceptance items 10 and 20 (test names `test_a10_*`, `test_a20_*`).
./orchestration/tickets/T06-sprints/ticket.md:25:- ruff + mypy strict clean; both named tests green.
./orchestration/tickets/T02-verify/plan-review.md:5:1. **Gate order** — accepted. Skip-scan is a preflight, then gates run in the spec's stated order: ruff, mypy, unit, build check, e2e.
./orchestration/tickets/T02-verify/plan-review.md:6:2. **Scoreboard suppressed on scan abort** — accepted. On a scan violation, verify prints the explicit message naming file+pattern AND still prints the full 36-item scoreboard (all FAIL — a tainted suite has no valid results) and `VERIFY: 0/36 PASS`.
./orchestration/tickets/T02-verify/plan-review.md:7:3. **Substring token matching** — accepted. Matching is anchored: a test satisfies item NN iff its name starts with `test_aNN_` / `test_eNN_` (underscore required after the token).
./orchestration/tickets/T02-verify/plan-review.md:9:5. **Item-21 coverage vs six patterns** — accepted for the scan (implements all six: skip mark, pytest.skip(, xfail, .only, commented-out test, empty body). The item-21 named test asserts at minimum the four patterns the item text lists, plus the remaining two — added assertions strengthen, never weaken.

codex
I’m checking a few mechanical properties next: executable bit, final-line behavior paths, and the scorer behavior on empty/missing junit inputs. This keeps the audit tied to code behavior instead of only reading comments.
exec
/bin/zsh -lc 'ls -l verify scripts/verify.py scripts/verify_lib.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
-rw-r--r--@ 1 khushaljagota  staff  5471 Jul  4 11:14 scripts/verify.py
-rw-r--r--@ 1 khushaljagota  staff  7908 Jul  4 11:13 scripts/verify_lib.py
-rwxr-xr-x@ 1 khushaljagota  staff   213 Jul  4 11:14 verify

codex
The sandbox is read-only, so shell here-docs and temporary synthetic files are blocked here. I’ll keep verification to import-free or no-write reads and use the implementation lines for the synthetic scan behavior.
exec
/bin/zsh -lc "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import sys; from pathlib import Path; sys.path.insert(0, str(Path(\"scripts\").resolve())); from verify_lib import ITEMS, score, parse_junit; print(len(ITEMS)); print([i.number for i in ITEMS if i.suite == \"unit\"]); print([i.number for i in ITEMS if i.suite == \"e2e\"]); print(sum(1 for r in score({}, {}) if r.passed)); print(parse_junit(Path(\"/tmp/nonexistent-hermes-verify-junit.xml\")))'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
36
[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 36]
[22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35]
0
{}

exec
/bin/zsh -lc "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import sys; from pathlib import Path; sys.path.insert(0, str(Path(\"scripts\").resolve())); from verify_lib import score; unit={\"x::test_a01_foo\":\"passed\",\"x::test_a02\":\"passed\",\"x::prefix_test_a03_foo\":\"passed\",\"x::test_a04_extra\":\"failed\",\"x::test_a05_one\":\"passed\",\"y::test_a05_two\":\"passed\"}; print({r.item.number:r.passed for r in score(unit,{}) if r.item.number <= 5})'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
{1: True, 2: False, 3: False, 4: False, 5: False}

codex
NO VIOLATIONS

Checked concretely:

1. Gate order is faithful: skip-scan preflight at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:111), then ruff, mypy, unit, build check, e2e at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:128).
2. All six forbidden patterns are implemented: text patterns at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:90), commented-out tests at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:149), empty bodies at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:108).
3. Anchored matching is correct: `token + "_"` and bare-name `startswith` at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:216).
4. Scoreboard prints one line per registry item and final `VERIFY: N/36 PASS`: [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:102), scan-fail path at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:123), normal path at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:150).
5. Exit 0 condition is correct: clean scan is required to reach gates, then `all(gate.ok)` and all 36 passing at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:154).
6. Missing/unparseable junit fails suite items without crashing: [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:189).
7. ITEMS registry matches SPEC 18.3 numbering: unit 1-21 and 36, e2e 22-35 at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:45). I also confirmed by import: 36 total, unit `[1..21, 36]`, e2e `[22..35]`.

Suggestion: align the wording between SPEC’s “every file in `assets/`” and the ticket/code’s `*.js` interpretation to remove ambiguity, though the implementation matches the ticket blueprint.
tokens used
64,758
NO VIOLATIONS

Checked concretely:

1. Gate order is faithful: skip-scan preflight at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:111), then ruff, mypy, unit, build check, e2e at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:128).
2. All six forbidden patterns are implemented: text patterns at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:90), commented-out tests at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:149), empty bodies at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:108).
3. Anchored matching is correct: `token + "_"` and bare-name `startswith` at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:216).
4. Scoreboard prints one line per registry item and final `VERIFY: N/36 PASS`: [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:102), scan-fail path at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:123), normal path at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:150).
5. Exit 0 condition is correct: clean scan is required to reach gates, then `all(gate.ok)` and all 36 passing at [scripts/verify.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify.py:154).
6. Missing/unparseable junit fails suite items without crashing: [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:189).
7. ITEMS registry matches SPEC 18.3 numbering: unit 1-21 and 36, e2e 22-35 at [scripts/verify_lib.py](/Users/khushaljagota/.hermes/planning-v2/scripts/verify_lib.py:45). I also confirmed by import: 36 total, unit `[1..21, 36]`, e2e `[22..35]`.

Suggestion: align the wording between SPEC’s “every file in `assets/`” and the ticket/code’s `*.js` interpretation to remove ambiguity, though the implementation matches the ticket blueprint.
