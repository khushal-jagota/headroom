# Hermes ACP connection

`create_hermes_backend_child_factory(launch)` supplies the Hermes backend child through the fixed conversation contract. The modules retain the Python extension path and the official ACP connection. The catalog source remains a separate part.

`hermes_acp_child_launch` retains the Python extension path. `child_environment` reproduces Python's six inherited variables, explicit launch overrides, full-access switch, and final conversation identity. Hermes does not use the tool-shell helper and does not add `BASH_ENV`. The Manager corrected that assumption in the ticket Brief.

`HermesAcpClient.begin_request` separates the completed stream write from the eventual response. Permission handlers return `{answer}` after admission so an unanswered request cannot block later notifications. `response_written` observes completion of that answer's actual stream write.

The connection pins `@agentclientprotocol/sdk` to **2.6.0**. It uses ACP **v1**, with the `/unstable` entrypoint because Python consumes prompt usage and session-info updates. This is not experimental wire v2.

The public SDK app starts asynchronous handlers without awaiting their completion, and responses bypass those handlers. This implementation therefore uses the SDK's own lower-level Connection and generated validators through version-pinned internal file URLs. An admission gate ahead of Connection orders inbound notifications, requests, and responses. The SDK still owns framing, protocol validation, request correlation, cancellation, and responders. Every SDK upgrade requires a new review of `client.ts`, its internal paths, and its ordering tests. Reply admission also waits for asynchronous prompt-ending and session-binding effects. Raw SDK response promises stay independent, so the ending reporter can consume its reply without a circular wait. Slow sink callbacks have no deadline. Shutdown and wire failure release their gates.

`HermesAcpLauncher` seals new starts before shutdown. It reuses Codex's process-group owner without a shared-helper change. Its bounded TERM/KILL cleanup follows that helper. A direct-child exit allows buffered ACP output to drain in order, within 15 seconds. Stop does not wait for descendant-held output pipes before it observes direct-child exit. Launch and cleanup errors use backend exception types rather than exposing Codex exceptions.

Strict whole-update validation uses the SDK's exported JSON schema before best-effort parsing. The Manager accepted the 15-second control-request deadlines and strict numeric-field validation on November 12, 2026, as recorded in `server/DEVIATIONS.md`. Regular persistence callbacks have no deadline. A control timeout settles that request only: steer becomes uncertain, configuration needs a fresh child, and startup reports session load failure.

## Evidence and remaining scope

`tests/compare/direct/test_unit16_launch.py` compares fresh Python and TS launch values with complete environments. It starts only a Node export evaluator, not the provider. No normalization applies.

The focused connection tests prove ordered split-frame handling, parked permissions and actual response writes, independent request timeout, broken-write settlement, and unchanged import environment. Launcher tests prove startup/shutdown races, failed-reader cleanup, and stdin errors. The pair now owns actual child environment and parent-mutation proof, malformed-notification admission, and retained usage, so the duplicate unit checks are removed. The fast-exit test needs one synthetic Node child because memory streams cannot prove OS child exit versus buffered output. Existing Codex process tests prove the reused process owner.

`tests/compare/direct/test_unit16_adapter.py` drives both real adapters and cores through the same scripted ACP peer. Nineteen scenarios compare persisted events, lifecycle facts, all five view tables, delivery fates, live frames, retained media hashes, and ordered ACP requests. Raw backend callback arrays remain diagnostic evidence. Their payloads and order within each event type compare exactly, but cross-type callback order is not a Python parity claim: Python dispatches callbacks concurrently. Dedicated TS-only checks prove the stronger ordered-admission rule required by the Brief.

The configuration-rebind case injects one managed-image read failure after model and effort changes, before prompt bytes reach the wire. This deterministic fault proves rollback and child replacement without a timing race against OS exit. Each scenario advances the synthetic clock beyond earlier scenarios before its maintenance sweep. No prior scenario can enter its compaction window.

`HermesModelCatalogOwner.source` supplies the unchanged catalog interface. `hermes_model_catalog_launch` retains the Python inventory script path. The owner copies the complete parent environment, applies Python's overrides, and returns raw command outcomes. The lifecycle owner must retain the object and call `shutdown()`, because the source interface stores only its callable. Cancellation stops current probes and permits a later read. Shutdown seals new reads before cleanup.

`tests/compare/direct/test_unit16_catalog.py` compares fresh Python launch values and command outcomes for success, failure, timeout, and malformed output. Four synthetic commands per language prove actual stdout, stderr, exit, and timeout behavior. Focused catalog tests cover cancellation, shutdown during spawn, and retry after failed cleanup.

Production lifecycle injection and separately authorized real-provider proof remain open. The side-by-side gate must confirm that real Hermes usage and cost events arrive and enter the records under strict validation. No real Hermes code runs in these tests.
