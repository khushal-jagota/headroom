# Browser review fix

## Files

- `src/planner/core/config.py` — added env-only `run_startup_recovery_in_test_mode`, gated by `PLAN_TEST_MODE`.
- `src/planner/core/server.py` — when that switch is enabled, FastAPI lifespan runs the existing startup human-chat recovery coordinator with the configured fake adapter.
- `tests/e2e/conftest.py` — added fixture-side pre-boot SQLite seeding and the startup-recovery switch env for only the opted-in server.
- `tests/e2e/test_live_chat_state.py` — added the browser proof for startup recovery of a stale ordinary ticket chat turn.
- `tests/unit/test_config.py` and `tests/unit/test_core_loops.py` — focused harness/config coverage for the new switch and fake-adapter startup recovery path.

## RED / GREEN

Focused config/harness:

```text
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/unit/test_config.py::test_startup_recovery_test_mode_switch_is_env_only_and_default_off
zsh:1: no such file or directory: .venv/bin/python
```

The isolated worktree had no local `.venv`; reran with the shared project venv:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_config.py::test_startup_recovery_test_mode_switch_is_env_only_and_default_off
.                                                                        [100%]
1 passed in 0.01s
```

Focused current Python proof:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_config.py::test_startup_recovery_test_mode_switch_is_env_only_and_default_off tests/unit/test_core_loops.py::test_test_mode_startup_recovery_switch_uses_configured_fake_adapter
..                                                                       [100%]
2 passed, 1 warning in 0.21s
```

Ruff after fixing import order:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m ruff check src/planner/core/config.py src/planner/core/server.py tests/e2e/conftest.py tests/e2e/test_live_chat_state.py tests/unit/test_config.py tests/unit/test_core_loops.py
All checks passed!
```

Frontend build:

```text
npm --prefix web run build
✓ 155 modules transformed.
✓ built in 720ms
```

Exact named Playwright test attempted:

```text
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/e2e/test_live_chat_state.py::test_startup_recovery_preserves_partial_chat_and_continues_without_duplicate_input
ERROR ... BrowserType.launch: Target page, context or browser has been closed
FATAL:base/apple/mach_port_rendezvous_mac.cc:159 ... Permission denied (1100)
```

Retries inside the Codex workspace sandbox with `--browser-channel=chrome` and `--headed` also failed before test execution with macOS Mach/Crashpad permission errors. `--browser=firefox` failed because Firefox is not installed in the Playwright cache.

The parent then ran the exact test from the normal ticket worktree (outside the Codex sandbox):

```text
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/e2e/test_live_chat_state.py::test_startup_recovery_preserves_partial_chat_and_continues_without_duplicate_input
.                                                                        [100%]
```

## Warnings

- The new Playwright test could not launch a browser inside Codex's managed sandbox, but it passed from the normal ticket worktree.
- The focused FastAPI lifespan test proves the new switch exercises the startup recovery coordinator with the configured fake adapter and settles the exact seeded transcript shape.
- `./verify` was not run, per instruction.
