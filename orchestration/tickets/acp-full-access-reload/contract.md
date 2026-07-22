# ACP full-access reload invariant

## Outcome

Every Panels-managed Worker and Chief runtime uses the backend's unrestricted permission mode
when its ACP session is first created and whenever its durable session is loaded into a replacement
child. A Panels restart, child failure, requested-cancel replacement, compaction handoff, or binding
adoption must not restore a provider's interactive permission default.

## Contracts

- `src/planner/conversation/employee_configuration.py`
  - Session configuration distinguishes initial launch choices from the fixed permission mode.
  - Initial configuration may apply model and reasoning choices plus permission mode.
  - Loaded-session configuration applies only permission mode. It never reapplies model or reasoning.
- `src/planner/conversation/employee_registry.py`
  - Every successful durable-session load configures the loaded session's fixed permission mode before
    the child can be published or prompted.
- Provider definitions and adapters
  - Codex starts with `agent-full-access` and enforces `agent-full-access` after load.
  - Claude uses `bypassPermissions` as the effective default and enforces it after load.
  - Hermes keeps `HERMES_YOLO_MODE=1` and enforces `dont_ask` after load.

## Public test seams

1. Provider definition/environment: the constructed backend starts with its unrestricted native
   default where the adapter exposes one.
2. Registry lifecycle: create a session, replace its child, load the durable binding, and observe the
   provider's unrestricted mode being applied after load while model and reasoning setters are absent.
3. Recovery variants: direct binding adoption and requested-cancel replacement cannot publish a
   loaded child before unrestricted mode configuration succeeds.

## Acceptance gates

- Focused Codex, Claude, Hermes employee-configuration and backend-definition tests.
- Focused employee-registry recovery tests covering every direct `capture_load_session` path.
- Ruff and strict Mypy for touched source and tests.
- Independent implementation-diff review with no unresolved contract violation.
- One canonical `./verify` on the settled tree.

## Exclusions

- Do not persist a permission field on Tickets, bindings, managed Worker settings, or Chief settings.
- Do not reapply or change model and reasoning selections when loading an existing durable session.
- Do not change the interactive browser permission broker.
