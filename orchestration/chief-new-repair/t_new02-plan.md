# t_new02 implementation plan

1. Extend `resolve_planner_home` with a keyword-only caller default while preserving selection order:
   explicit value, `PLAN_HERMES_HOME`, caller default. Its standalone default remains
   `data/hermes-home` and final values retain existing `~` expansion.
2. In non-test server startup, compute the implicit default as the absolute, non-strict resolution of
   `<configured database path parent>/hermes-home`, then give it to the resolver. Pass the one resolved
   home to skill provisioning and both role gateways.
3. Expand resolver tests for standalone default, caller default, environment override, explicit-value
   override, and `~` expansion.
4. Add a non-test lifespan regression with an absolute database under one root and cwd in another
   worktree. Capture provisioning and gateway construction and assert both receive the absolute
   database-adjacent home, never cwd-local `data/hermes-home`. Adjust the existing monkeypatch to accept
   the new resolver keyword.
5. Update `docs/employee-runtime.md`: the configured database owns the implicit home location; explicit
   `PLAN_HERMES_HOME` wins; startup links only role skills and never copies credentials/provider config.
