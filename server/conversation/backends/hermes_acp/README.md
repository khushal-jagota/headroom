# Hermes ACP connection

This is the first part of the Hermes port. It supplies launch construction, the ACP connection, and child ownership. It does not yet supply a `BackendChildFactory` or model-catalog source.

`hermes_acp_child_launch` retains the Python extension path. `child_environment` reproduces Python's six inherited variables, explicit launch overrides, full-access switch, and final conversation identity. Hermes does not use the tool-shell helper and does not add `BASH_ENV`. The Manager corrected that assumption in the ticket Brief.

`HermesAcpClient.begin_request` separates the completed stream write from the eventual response. Permission handlers return `{answer}` after admission so an unanswered request cannot block later notifications. `response_written` observes completion of that answer's actual stream write.

The connection pins `@agentclientprotocol/sdk` to **2.6.0**. It uses ACP **v1**, with the `/unstable` entrypoint because Python consumes prompt usage and session-info updates. This is not experimental wire v2.

The public SDK app starts asynchronous handlers without awaiting their completion, and responses bypass those handlers. This implementation therefore uses the SDK's own lower-level Connection and generated validators through version-pinned internal file URLs. An admission gate ahead of Connection orders inbound notifications, requests, and responses. The SDK still owns framing, protocol validation, request correlation, cancellation, and responders. An SDK update requires review of these internal paths and the ordering tests.

`HermesAcpLauncher` seals new starts before shutdown. It reuses Codex's process-group owner without a shared-helper change. Its bounded TERM/KILL cleanup follows that helper. A direct-child exit allows buffered ACP output to drain in order, within 15 seconds. Stop does not wait for descendant-held output pipes before it observes direct-child exit. Launch and cleanup errors use backend exception types rather than exposing Codex exceptions.

## Evidence and remaining scope

`tests/compare/direct/test_unit16_launch.py` compares fresh Python and TS launch values with complete environments. It starts only a Node export evaluator, not the provider. No normalization applies.

The focused connection tests prove ordered split-frame handling, parked permissions and actual response writes, independent request timeout, broken-write settlement, malformed-notification admission, retained prompt usage, and unchanged import environment. Launcher tests prove environment stability, startup/shutdown races, cleanup failure, and stdin errors. The fast-exit test needs one synthetic Node child because memory streams cannot prove OS child exit versus buffered output. Existing Codex process tests prove the reused process owner.

These connection tests are TS-only. They do not prove core-record, view, lifecycle, or delivery-fate parity. The later backend-child part owns that paired scripted proof. The catalog binding, production lifecycle injection, and separately authorized real-provider proof remain open. No real Hermes code runs in these tests.
