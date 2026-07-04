# harness-prep.md

Preparation state for the unattended /goal run. Two lists: what only a human can do, and the agent preflight (every item a command with a PASS/FAIL result). The build itself needs no live external services — hermes spawn, boundary/replan agents, and the chat gateway are faked in all tests per SPEC.md §14/§18.3.

## Human-only items

None. There are no auth-walled dependencies in the build: no external APIs are called, the Codex CLI is already authenticated (verified below), and live Hermes integration is explicitly a post-run human pass (SPEC.md §18.4), not part of the goal. If the Codex READY check below ever fails with an auth error, logging into the Codex CLI becomes the single human-only item.

## Agent preflight

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Required docs exist | `ls SPEC.md CLAUDE.md codex-audit.md GOAL-CONDITION.md harness-prep.md` | PASS |
| 2 | Python ≥ 3.12 | `python3 --version` → 3.14.3 | PASS |
| 3 | Venv + pinned deps install | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (39 pins frozen) | PASS |
| 4 | Backend connects non-interactively | `.venv/bin/python -c "sqlite3 WAL smoke"` → `SQLITE-WAL-OK 42 wal` | PASS |
| 5 | Server framework imports | `.venv/bin/python -c "import fastapi, uvicorn, httpx, yaml"` | PASS |
| 6 | Playwright + chromium installed | `.venv/bin/playwright install chromium` + headless launch → `CHROMIUM-OK` | PASS |
| 7 | Node present (asset syntax checks) | `node --version` → v22.22.3 | PASS |
| 8 | Codex CLI answers | `codex exec "reply with only the word READY"` → `READY` | PASS |
| 9 | External APIs required by build | none — adapters faked in tests | N/A |
| 10 | Git clean on intended branch (`main`) | `git status --porcelain` empty, branch `main` | PASS (after initial commit) |

Preflight executed 2026-07-04. All checks pass; no human-only items outstanding.
