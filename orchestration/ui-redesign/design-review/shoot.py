"""Design-review screenshot harness (orchestrator tooling, not product code).

Boots a real `panels serve` on a free port with a temp DB (same env recipe as
tests/e2e/conftest.py), seeds demo content through the CLI + HTTP API, then
screenshots each app surface AND the corresponding reference mockup from
orchestration/daily-redesign/, side by side into an output directory.

Run serialized (never concurrently with ./verify — Playwright contention):
    .venv/bin/python orchestration/ui-redesign/design-review/shoot.py <outdir>
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"
MOCKS = REPO_ROOT / "orchestration" / "daily-redesign"
FAKE_NOW = "2026-07-10T09:30:00"
VIEWPORT = {"width": 1440, "height": 900}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scrubbed_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}
    env.update(extra)
    return env


def cli(base: str, *args: str, ticket_id: str | None = None, stdin: str | None = None) -> dict:
    env = scrubbed_env({"PLAN_SERVER_URL": base})
    if ticket_id is not None:
        env["PLAN_TICKET_ID"] = ticket_id
    proc = subprocess.run(
        [str(PLAN_BIN), *args, "--json"],
        input=stdin, capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"panels {' '.join(args)} rc={proc.returncode}\n{proc.stderr}")
    return json.loads(proc.stdout)


def post(base: str, path: str, body: dict) -> dict:
    resp = httpx.post(base + path, json=body, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def patch(base: str, path: str, body: dict) -> dict:
    resp = httpx.patch(base + path, json=body, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def boot(tmp: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env({
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(tmp / "planning.db"),
        "PLAN_PORT": str(port),
        "PLAN_FAKE_NOW": FAKE_NOW,
        "PLAN_LOGS_DIR": str(tmp / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "dispatcher.lock"),
        "PLAN_WS_POLL_MS": "50",
        "PLAN_UI_DEBOUNCE_MS": "50",
    })
    log = (tmp / "server.log").open("wb")
    proc = subprocess.Popen([str(PLAN_BIN), "serve"], cwd=str(REPO_ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited rc={proc.returncode}; see {tmp}/server.log")
        try:
            if httpx.get(f"{base}/api/meta", timeout=1.0).status_code == 200:
                return proc, base
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("server boot timeout")


def seed(base: str) -> dict[str, str]:
    """Best-effort demo world mirroring the mockup content. Returns ids."""
    ids: dict[str, str] = {}
    # Projects
    for name in ("Panels", "Vylo"):
        try:
            cli(base, "project", "create", "--name", name)
        except RuntimeError as err:
            print(f"[seed] project {name}: {err}", file=sys.stderr)

    # The hero ticket: doorbell with success/approach set and a pending plan proposal.
    t = cli(base, "ticket", "create", "--title", "Readiness doorbell for the ticket loop")
    tid = ids["ticket"] = t["id"]
    patch(base, f"/api/tickets/{tid}", {"priority": "P1", "deadline": "2026-07-14"})
    # No scope pre-set: proposals inside the ceiling auto-resolve, so the gates
    # are walked one accept at a time to leave a pending plan proposal at the end.

    def propose(body: str) -> None:
        cli(base, "worker", "propose", tid, "--body-file", "-",
            "--recap", "Success and approach agreed; the loop wakes on a doorbell instead of polling.",
            stdin=body)

    def accept(field: str, next_ceiling: str) -> None:
        post(base, f"/api/tickets/{tid}/accept/{field}",
             {"next_ceiling": next_ceiling, "at_cap": "propose"})

    propose("A runnable ticket is picked up within a second of becoming runnable, with zero polling queries at idle.")
    accept("success", "needs_approach")
    propose("One awaitable doorbell owned by the runtime; writers ring it after commit. A slow heartbeat stays as the backstop.")
    accept("approach", "needs_plan")
    propose("Replace the poll with a doorbell, in four steps:\n\n"
            "1. Add `readiness_doorbell.py` — a single awaitable the loop parks on.\n"
            "2. Ring it from the three writer actions that can make a ticket runnable.\n"
            "3. Keep one slow heartbeat as a backstop so a missed ring can never strand a ticket.\n"
            "4. Port the loop tests to the doorbell and delete the poll-interval knobs.\n")
    patch(base, f"/api/tickets/{tid}", {"user_note": "Keep this to the doorbell itself. The runtime loop has its own ticket."})

    # Supporting cast for the roster.
    for title in ("Deepen employee runtime ownership", "Chat auto-scroll and Latest button",
                  "Ship the waitlist capture flow", "Consent copy for double opt-in"):
        c = cli(base, "ticket", "create", "--title", title)
        ids[title] = c["id"]

    # Day brief.
    try:
        patch(base, "/api/day/2026-07-10", {
            "focus": "Clear the two plans and the doorbell ships today.",
            "brief_take": "Quiet night. Three tickets moved and nothing broke that stays broken.",
            "watchout": "Consent copy errored on a missing legal source and will stay stuck until you point it somewhere.",
            "if_today_lands": "The doorbell is building and the consent question has an owner.",
        })
    except Exception as err:  # noqa: BLE001
        print(f"[seed] day: {err}", file=sys.stderr)

    # Ideas + backlog (API-first, CLI fallback).
    for title, body in (("Ticket templates for recurring work",
                         "A worker skill that clones a past ticket's success and approach."),
                        ("Weekly digest email of settled tickets", "")):
        try:
            post(base, "/api/ideas", {"title": title, **({"body": body} if body else {})})
        except Exception as err:  # noqa: BLE001
            print(f"[seed] idea: {err}", file=sys.stderr)
    return ids


SURFACES = [
    ("day", "#/day", "day.html"),
    ("review", "#/review", "review.html"),
    ("workspace", "#/workspace", "workspace.html"),
    ("ticket", "#/ticket/{ticket}", "ticket.html"),
    ("sprint", "#/sprint", "sprint.html"),
    ("backlog", "#/backlog", "backlog.html"),
    ("ideas", "#/ideas", "ideas.html"),
]


def main() -> None:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/design-review")
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir / "srv"
    proc, base = boot(tmp)
    try:
        ids = seed(base)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport=VIEWPORT)
            for name, route, mock in SURFACES:
                url = base + "/" + route.format(**ids) if "{" in route else base + "/" + route
                page.goto(url)
                page.wait_for_timeout(1200)
                page.screenshot(path=str(outdir / f"app-{name}.png"), full_page=True)
                mock_path = MOCKS / mock
                if mock_path.exists():
                    page.goto(mock_path.as_uri())
                    page.wait_for_timeout(600)
                    page.screenshot(path=str(outdir / f"mock-{name}.png"), full_page=True)
            browser.close()
        print(f"screenshots in {outdir}")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
