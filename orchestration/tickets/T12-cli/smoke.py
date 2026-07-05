#!/usr/bin/env python3
"""T12 CLI-wiring self-smoke. Boots a real test-mode server on a temp DB and drives
the real `plan` binary over HTTP, asserting each §8 acceptance line: create -> propose
(env-pinned, multi-line stdin) -> visible pending proposal -> approvals queue; item
create/set; day add/show/remove via `today`; link add/rm; and the exit-code contract
(client validation -> 1, server error -> 1, dead port -> 2).

Standalone script (run by .venv/bin/python), not a pytest test — it lives outside
tests/, so ./verify's skip-scan and 36-item scoreboard never see it. Prints per-step
PASS/FAIL; exits 0 only when every step passes. The server is always terminated and the
temp dir removed in finally; the captured server log is dumped on any failure. Both the
server env and the CLI env are scrubbed of inherited PLAN_* vars, then given back only
what each needs, so a polluted outer environment cannot skew the run."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
PLAN = REPO / ".venv/bin/plan"
TIMEOUT = 30.0

_FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"{'PASS' if cond else 'FAIL'} {name}")
    if not cond:
        _FAILS.append(name)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}


def dump_server(log_path: Path) -> None:
    sys.stderr.write("----- server log -----\n")
    sys.stderr.write(log_path.read_text() if log_path.exists() else "(no server log)\n")
    sys.stderr.write("----------------------\n")


def wait_ready(base: str, proc: subprocess.Popen[str], budget: float = 15.0) -> None:
    deadline = time.time() + budget
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("server exited before becoming ready")
        try:
            with urllib.request.urlopen(f"{base}/api/meta", timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.2)
    raise AssertionError("server did not become ready within budget")


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="t12-smoke-"))
    log_path = tmpdir / "server.log"
    port = free_port()
    dead_port = free_port()
    base = f"http://127.0.0.1:{port}"

    server_env = {
        **scrubbed_env(),
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(tmpdir / "planning.db"),
        "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(tmpdir / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmpdir / "dispatcher.lock"),
    }
    base_env = {**scrubbed_env(), "PLAN_SERVER_URL": base}

    def run_plan(
        args: list[str], *, env_extra: dict[str, str] | None = None, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {**base_env, **(env_extra or {})}
        if stdin is None:
            return subprocess.run(
                [str(PLAN), *args], cwd=str(REPO), env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=TIMEOUT,
            )
        return subprocess.run(
            [str(PLAN), *args], cwd=str(REPO), env=env, input=stdin,
            capture_output=True, text=True, timeout=TIMEOUT,
        )

    logf = log_path.open("w")
    proc = subprocess.Popen(
        [str(PLAN), "serve"], cwd=str(REPO), env=server_env, stdout=logf,
        stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_ready(base, proc)
        check("0 server booted; /api/meta 200", True)

        # 1. ticket create --json -> id.
        r = run_plan(["ticket", "create", "--title", "Smoke A", "--json"])
        tid = json.loads(r.stdout)["id"] if r.returncode == 0 else ""
        check("1 ticket create --json -> id", r.returncode == 0 and bool(tid))

        # 2. env-pinned propose, multi-line stdin; also the no-`--json` terse-line check.
        body = "line one\n\nline two\n"
        r = run_plan(["propose", "success", "-"], env_extra={"PLAN_TICKET_ID": tid}, stdin=body)
        check("2 propose success (stdin body, env id) -> exit 0, terse line",
              r.returncode == 0 and tid in r.stdout)

        # 3. ticket show --json — pending proposal at its exact key, body verbatim.
        r = run_plan(["ticket", "show", "--json"], env_extra={"PLAN_TICKET_ID": tid})
        d: Any = json.loads(r.stdout)
        check("3 ticket show --json -> fields.success.proposal.body verbatim",
              r.returncode == 0 and d["fields"]["success"]["proposal"]["body"] == body)

        # 4. queue approvals --json lists it (A3: object keyed "approvals").
        r = run_plan(["queue", "approvals", "--json"])
        rows = json.loads(r.stdout)["approvals"]
        check("4 queue approvals --json lists the ticket",
              r.returncode == 0 and tid in [e["entity_id"] for e in rows])

        # 5. item create + set.
        iid = json.loads(
            run_plan(["item", "create", "--title", "Item A", "--project", "Vylo", "--json"]).stdout
        )["id"]
        d = json.loads(run_plan(["item", "set", iid, "--status", "active", "--json"]).stdout)
        check("5 item create + set --status active", d["status"] == "active")

        # 6. day add-ticket / show / remove-ticket (literal `today`).
        d = json.loads(run_plan(["day", "add-ticket", tid, "today", "--json"]).stdout)
        add_ok = tid in [t["id"] for t in d["tickets"]]
        d = json.loads(run_plan(["day", "show", "today", "--json"]).stdout)
        show_ok = tid in [t["id"] for t in d["tickets"]]
        d = json.loads(run_plan(["day", "remove-ticket", tid, "today", "--json"]).stdout)
        rm_ok = tid not in [t["id"] for t in d["tickets"]]
        check("6 day add/show/remove-ticket (today)", add_ok and show_ok and rm_ok)

        # 7. link add / rm.
        tid2 = json.loads(
            run_plan(["ticket", "create", "--title", "Smoke B", "--json"]).stdout
        )["id"]
        d = json.loads(run_plan(["link", "add", tid2, tid, "--kind", "blocks", "--json"]).stdout)
        add_ok = d["kind"] == "blocks"
        d = json.loads(run_plan(["link", "rm", tid2, tid, "--kind", "blocks", "--json"]).stdout)
        rm_ok = d.get("ok") is True
        check("7 link add/rm", add_ok and rm_ok)

        # 8. client-side validation error -> exit 1 + parseable {"error":...} on stderr.
        r = run_plan(["propose", "success", "-", "--json"],
                     env_extra={"PLAN_TICKET_ID": tid}, stdin="")
        check("8 empty body -> exit 1, stderr error.code == validation",
              r.returncode == 1 and json.loads(r.stderr)["error"]["code"] == "validation")

        # 9. server structured error -> exit 1 (HTTP path through http.send).
        r = run_plan(["ticket", "show", "t_does_not_exist", "--json"])
        check("9 server not_found -> exit 1, stderr error.code == not_found",
              r.returncode == 1 and json.loads(r.stderr)["error"]["code"] == "not_found")

        # 10. dead port -> exit 2 (transport).
        r = run_plan(["ticket", "show", tid, "--json"],
                     env_extra={"PLAN_SERVER_URL": f"http://127.0.0.1:{dead_port}"})
        check("10 dead port -> exit 2", r.returncode == 2)

        # 11. PLAN_TICKET_ID='-' is never a stdin marker nor a ticket id (codex finding):
        # propose must NOT read the piped stdin as body (no explicit source -> exit 1),
        # and ticket show must reject the bogus env id client-side (exit 1).
        r = run_plan(["propose", "success", "--json"],
                     env_extra={"PLAN_TICKET_ID": "-"}, stdin="not a body\n")
        propose_ok = r.returncode == 1 and json.loads(r.stderr)["error"]["code"] == "validation"
        r = run_plan(["ticket", "show", "--json"], env_extra={"PLAN_TICKET_ID": "-"})
        show_ok = r.returncode == 1 and json.loads(r.stderr)["error"]["code"] == "validation"
        check("11 PLAN_TICKET_ID='-' rejected (no implicit stdin, no '-' id)",
              propose_ok and show_ok)

        if _FAILS:
            dump_server(log_path)
            print(f"SMOKE FAIL ({len(_FAILS)}): {', '.join(_FAILS)}")
            return 1
        print("SMOKE OK")
        return 0
    except BaseException:
        dump_server(log_path)
        raise
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        logf.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
