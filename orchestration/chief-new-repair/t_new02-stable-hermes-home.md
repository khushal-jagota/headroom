# t_new02 — Stable Hermes home

Reference: `src/planner/minds/config.py`, `src/planner/core/server.py`, and
`src/planner/core/config.py` (`Config.db_path`).

## Outcome

A Panels process serving a configured planning database uses the Hermes home beside that database,
even when the executable source and current working directory are in another git worktree.

## Contract

1. Preserve resolution precedence: explicit function value, then `PLAN_HERMES_HOME`, then the caller's
   default.
2. Server composition supplies `<configured database directory>/hermes-home` as that default. Resolve
   it to an absolute path before constructing role gateways so a later cwd change cannot retarget it.
3. An explicit `PLAN_HERMES_HOME` continues to win unchanged.
4. Skill provisioning stays limited to the three repository role-skill symlinks. Do not copy, link,
   or mutate credentials, provider configuration, or global Hermes state.
5. The standalone resolver keeps its existing `data/hermes-home` default when no caller-specific
   default is supplied.

## Owned files

- `src/planner/minds/config.py`
- `src/planner/core/server.py`
- `tests/unit/test_minds.py`
- the most focused server-composition test if needed
- `docs/employee-runtime.md`

## Acceptance

- A regression with an absolute database path and a different cwd proves the role gateways receive
  the database-adjacent absolute Hermes home.
- Resolver tests prove explicit value and environment precedence.
- No test or production code reads credentials or provider config.
