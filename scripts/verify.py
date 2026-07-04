"""The verify instrument — the single source of truth for completeness (SPEC 18.2).

Runs a preflight skip-scan of tests/, then the code and test gates in the spec's
stated order (ruff, mypy, unit suite, build check, e2e suite), streaming each
gate's output live. Ends with a 36-item scoreboard and exactly one final line,
``VERIFY: N/36 PASS``. Exit is 0 iff the scan is clean, every gate passed, and
N == 36.

Documented limitation: this instrument proves the named tests RAN and PASSED. It
does not judge assertion strength — that is enforced by the SPEC 18.3 fences
(exact states, orderings, and error shapes) and the independent codex audit, not
here.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from verify_lib import ITEMS, ItemResult, parse_junit, scan_test_files, score

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_BIN = REPO_ROOT / ".venv" / "bin"
DATA_VERIFY = REPO_ROOT / "data" / "verify"

# Generous ceiling so a hung server can never wedge verify forever.
E2E_TIMEOUT_SECONDS = 1800


@dataclass
class GateResult:
    name: str
    ok: bool
    detail: str = ""


def _run(cmd: list[str], timeout: int | None = None) -> tuple[int, bool]:
    """Run a gate command with inherited stdio (live streaming).

    Returns (returncode, timed_out). Never raises: a missing command or a
    timeout is reported as a failed gate, not a crash.
    """
    printable = " ".join(str(c) for c in cmd)
    print(f"\n=== {printable} ===", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, timeout=timeout)
        return proc.returncode, False
    except subprocess.TimeoutExpired:
        print(f"[verify] gate timed out after {timeout}s and was killed", flush=True)
        return -1, True
    except FileNotFoundError as exc:
        print(f"[verify] gate command not found: {exc}", flush=True)
        return -1, False


def run_ruff() -> GateResult:
    rc, _ = _run([str(VENV_BIN / "ruff"), "check", "."])
    return GateResult("ruff", rc == 0)


def run_mypy() -> GateResult:
    rc, _ = _run([str(VENV_BIN / "mypy"), "src/"])
    return GateResult("mypy", rc == 0)


def run_pytest(target: str, junit: Path, name: str, timeout: int | None = None) -> GateResult:
    """Run a pytest suite, writing junit XML for scoring.

    A stale junit from a prior run is removed first so a crash or timeout that
    writes nothing scores the suite's items FAIL rather than reusing old data.
    Exit code 5 (no tests collected) is tolerated at stage 2: the gate is not
    green, but it is not a crash either.
    """
    if junit.exists():
        junit.unlink()
    cmd = [str(VENV_BIN / "pytest"), target, f"--junitxml={junit}"]
    rc, timed_out = _run(cmd, timeout=timeout)
    if timed_out:
        return GateResult(name, False, "timed out")
    if rc == 5:
        return GateResult(name, False, "no tests collected")
    return GateResult(name, rc == 0)


def run_build_check() -> GateResult:
    """compileall over src/ plus node --check on every *.js under assets/."""
    ok = True
    rc, _ = _run([sys.executable, "-m", "compileall", "-q", "src/"])
    if rc != 0:
        ok = False
    assets = REPO_ROOT / "assets"
    js_files = sorted(assets.rglob("*.js")) if assets.is_dir() else []
    for js in js_files:
        rc_js, _ = _run(["node", "--check", str(js)])
        if rc_js != 0:
            ok = False
    return GateResult("build check", ok)


def print_scoreboard(results: list[ItemResult]) -> None:
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        print(f"[{tag}] item {r.item.number:02d} — {r.item.label}")


def main() -> int:
    DATA_VERIFY.mkdir(parents=True, exist_ok=True)

    # Preflight skip-scan. A tainted suite has no valid results, so on any
    # violation we name each file+pattern, skip the suites, and still print the
    # full all-FAIL scoreboard.
    violations = scan_test_files([REPO_ROOT / "tests"])
    if violations:
        print("[verify] SKIP-SCAN FAILED — forbidden patterns in tests/:", flush=True)
        for v in violations:
            print(f"    {v.file}: {v.pattern}", flush=True)
        print(
            "[verify] test suites skipped; a tainted suite has no valid results.",
            flush=True,
        )
        print_scoreboard([ItemResult(item, False) for item in ITEMS])
        print("VERIFY: 0/36 PASS")
        return 1

    gates: list[GateResult] = []
    gates.append(run_ruff())
    gates.append(run_mypy())

    unit_junit = DATA_VERIFY / "unit.xml"
    gates.append(run_pytest("tests/unit", unit_junit, "unit suite"))

    gates.append(run_build_check())

    e2e_junit = DATA_VERIFY / "e2e.xml"
    gates.append(
        run_pytest("tests/e2e", e2e_junit, "e2e suite", timeout=E2E_TIMEOUT_SECONDS)
    )

    results = score(parse_junit(unit_junit), parse_junit(e2e_junit))

    print()
    for gate in gates:
        status = "ok" if gate.ok else "FAILED"
        suffix = f" ({gate.detail})" if gate.detail else ""
        print(f"[verify] gate {gate.name}: {status}{suffix}")
    print()

    print_scoreboard(results)
    passed = sum(1 for r in results if r.passed)
    print(f"VERIFY: {passed}/36 PASS")

    ok = all(gate.ok for gate in gates) and passed == len(ITEMS)
    return 0 if ok else 1


if __name__ == "__main__":
    assert len(ITEMS) == 36, f"item registry must hold 36 items, has {len(ITEMS)}"
    sys.exit(main())
