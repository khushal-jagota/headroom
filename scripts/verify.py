"""The verify instrument — complete by default, with explicit focused tiers.

``./verify`` or ``./verify full`` runs every gate and is the sole completeness
claim. During implementation, run focused gates for the behavior being changed.
The explicit ``fast``, ``e2e``, and ``integration`` modes remain available for
bounded suite runs when their whole surface is relevant.

Documented limitation: this instrument proves the named tests RAN and PASSED. It
does not judge assertion strength — that is for reviewers, not this instrument.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from verify_lib import check_css_syntax, parse_verify_mode

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
    # No paths: what gets checked is stated once, by ``files`` in pyproject.toml, so this
    # gate and a bare ``mypy`` at a terminal can never disagree about the surface. That
    # surface is src/ plus the whole of tests/ — production code, the strict-mypy fixtures
    # under tests/typing/ that are checked rather than run, and every real test, whose
    # fixture parameters are the objects under test.
    rc, _ = _run([str(VENV_BIN / "mypy")])
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
    """compileall over src/ plus a syntax check on EVERY file under assets/:
    node --check per *.js, check_css_syntax per *.css, and any other file type
    fails the gate outright."""
    ok = True
    rc, _ = _run([sys.executable, "-m", "compileall", "-q", "src/"])
    if rc != 0:
        ok = False
    assets = REPO_ROOT / "assets"
    files = sorted(p for p in assets.rglob("*") if p.is_file()) if assets.is_dir() else []
    for path in files:
        if path.suffix == ".js":
            rc_js, _ = _run(["node", "--check", str(path)])
            if rc_js != 0:
                ok = False
        elif path.suffix == ".css":
            print(f"\n=== css check {path} ===", flush=True)
            errors = check_css_syntax(path.read_text(encoding="utf-8"))
            for error in errors:
                print(f"[verify] {path}: {error}", flush=True)
            if errors:
                ok = False
        else:
            print(f"[verify] unexpected file type in assets/: {path}", flush=True)
            ok = False
    return GateResult("build check", ok)


def run_frontend() -> GateResult:
    web = REPO_ROOT / "web"
    if not (web / "package.json").exists():
        return GateResult("frontend", True)

    ok = True
    if not (web / "node_modules").exists():
        if not (web / "package-lock.json").exists():
            return GateResult("frontend", True, "skipped: npm dependencies unavailable")
        rc_install, _ = _run(["npm", "--prefix", "web", "ci"])
        if rc_install != 0:
            ok = False
    for cmd in (
        ["npm", "--prefix", "web", "run", "check"],
        ["npm", "--prefix", "web", "run", "build"],
        ["npm", "--prefix", "web", "test"],
    ):
        rc, _ = _run(cmd)
        if rc != 0:
            ok = False
    return GateResult("frontend", ok)


def main() -> int:
    try:
        mode = parse_verify_mode(sys.argv[1:])
    except ValueError as exc:
        print(exc)
        print("VERIFY: FAIL")
        return 2

    DATA_VERIFY.mkdir(parents=True, exist_ok=True)

    gates: list[GateResult] = []
    if mode in {"full", "fast"}:
        gates.append(run_ruff())
        gates.append(run_mypy())

        unit_junit = DATA_VERIFY / "unit.xml"
        gates.append(run_pytest("tests/unit", unit_junit, "unit suite"))

        gates.append(run_build_check())
        gates.append(run_frontend())

    if mode in {"full", "integration"}:
        integration_junit = DATA_VERIFY / "integration.xml"
        gates.append(
            run_pytest(
                "tests/integration",
                integration_junit,
                "integration suite",
                timeout=E2E_TIMEOUT_SECONDS,
            )
        )

    if mode in {"full", "e2e"}:
        e2e_junit = DATA_VERIFY / "e2e.xml"
        gates.append(
            run_pytest("tests/e2e", e2e_junit, "e2e suite", timeout=E2E_TIMEOUT_SECONDS)
        )

    print()
    for gate in gates:
        status = "ok" if gate.ok else "FAILED"
        suffix = f" ({gate.detail})" if gate.detail else ""
        print(f"[verify] gate {gate.name}: {status}{suffix}")
    print()

    ok = all(gate.ok for gate in gates)
    label = "VERIFY" if mode == "full" else f"VERIFY {mode.upper()}"
    print(f"{label}: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
