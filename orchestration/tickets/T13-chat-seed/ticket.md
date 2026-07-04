# T13 — Chat service and seed endpoint (stage 4)

## Scope

The chat passthrough and the seed API. Small ticket.

Contracts: `chat/contracts.py`, `seed/` importer+demo (T07), adapter registry (gateway), `core/authctx.py`. SPEC §11, §12.

## Files owned

- `src/planner/chat/service.py` — send(entity_id, text): resolve entity (ticket `t_`/day `day_` prefix), pass its `chat_session_key` to the gateway adapter, persist a newly minted session key on first reply (`chat_session_created` event), return reply + key. Gateway unavailable → `gateway_offline` structured error (the panel renders the notice; the rest of the UI unaffected).
- `src/planner/chat/api.py` — `POST /api/chat/{entity_id}/send`, `GET /api/chat/{entity_id}/status` over the service.
- `src/planner/seed/api.py` — `POST /api/seed` `{source_dir}` or `{demo: true}` → runs importer/demo inside the server process against its DB, returns the MigrationReport JSON (structured shape from seed contracts). Path validation: source_dir must exist and be a directory; never resolves outside given path semantics (no reaching into `~/.hermes/planning/` by default anywhere in code).

## Acceptance for integration

Self-smoke: echo-gateway server → send on a ticket returns `echo: <text>` and persists session key (visible via ticket JSON); offline-gateway server → 503 gateway_offline and status `{available: false}`; `POST /api/seed {demo}` on empty temp DB → report with the §12 demo counts; second demo call → db_not_empty error. ruff + mypy strict clean; unit suite stays green.
