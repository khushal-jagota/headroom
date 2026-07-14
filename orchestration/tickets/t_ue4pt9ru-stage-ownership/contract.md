# Stage ownership implementation contract

Ticket: `t_ue4pt9ru`

This contract freezes the approved Success, Approach, and Plan for implementation. Ownership is separate from `ceiling + at_cap`; the stored `at_cap=propose` wire value and backend behavior stay unchanged while the UI label becomes **Continue**.

## Public vocabulary and storage

- `StageOwnershipMode`: `worker | user | paired`.
- Every non-terminal `StageDefinition` has `default_ownership_mode`. `done` and `dropped` have none. All currently shipped coding and new-worker Stages default to `worker` so existing behavior is preserved.
- A Ticket stores `stage_ownership_overrides` as a JSON object keyed by one of its Worker type's non-terminal Stage ids. Values are ownership modes. Missing key means use the Stage default.
- A Ticket exposes `default_stage_ownership_mode` and `effective_stage_ownership_mode` for its current Stage, plus the override map. Terminal Tickets expose both current values as null.
- The Worker-type manifest exposes `default_ownership_mode` on every Stage (null for terminals).
- Add durable `ticket_status=paired_work`. Keep `user_takeover`; do not add a generic waiting status.
- Replace `Implementer`/`implementer` with `ExecutionRoute`/`execution_route`. Valid routes are `panels_worker | hermes_codex | hermes_claude`; `khushal` is not an execution route.
- Add `stage_ownership_changed` as the one event for setting or clearing an override. Payload: `stage`, `ownership_mode` (null means cleared), and `effective_ownership_mode`.

## Migration

Add a new terminal migration after v20; do not edit or fold behavior into the sealed v20 migrator.

The migration rebuilds `tickets` under the established exclusive-lock/foreign-key-safe swap and:

- preserves every existing stage, scope, field, Employee session, and active status exactly;
- copies agent implementers to `execution_route`;
- maps every coding Ticket with `implementer=khushal` to `execution_route=NULL` plus `stage_ownership_overrides["needs_implementation"]="user"`, regardless of its current Stage;
- maps a non-coding `khushal` value to `execution_route=NULL` with no invented ownership override because it had no defined transition behavior;
- initializes all other override maps to `{}`;
- expands the status CHECK for `paired_work` and removes the old implementer column/check.

## Effective ownership and resting control

One framework-free rule resolves current ownership: current Stage override first, otherwise Stage default. A second rule derives an inactive Ticket's resting status:

- `worker` -> `empty`;
- `user` -> `user_takeover`;
- `paired` -> `paired_work`.

Use the rule after a Stage changes, external-work reconciliation, every successful worker-run settlement that clears `agent_running_step` (whether or not the run filed or auto-accepted a proposal), an ownership override changes, Take over, and Release. Do not overwrite a live `agent_running_step`, pending `awaiting_approval`, or `errored` state merely because a row is read or migrated. If Take over changes the override while a run is active, that run's eventual no-proposal settlement must preserve the effective user ownership instead of restoring `empty`. Ordinary Ticket creation still parks the Kickoff proposal at `awaiting_approval`.

Take over sets the current Stage override to `user` and preserves the existing ability to take control during an active run. Release clears the current Stage override and reapplies the Stage default. No override stack is introduced.

## Mode lifecycle

### Worker

Automatic eligibility requires effective ownership `worker` in addition to every existing membership, blocker, scope, status, active-turn, and proposal condition. Existing below-ceiling auto-accept and at-cap behavior remains unchanged.

### User

A user-owned Stage rests at `user_takeover` and is never automatically dispatched. Work completed by the user is recorded through the existing Chief external-work create/reconcile operation; no direct-settle or self-proposal path is added.

External-work reconciliation moves the ceiling to the reconciled Stage but preserves the Ticket's explicit `at_cap`: a Ticket already set to Stop remains Stop; otherwise the default `propose` wire value (shown as Continue) remains. It then derives the entered Stage's ownership status.

### Paired

A paired Stage rests at `paired_work` and is never automatically dispatched. Ordinary Ticket Chat is the user-originated continuation path: it must deliver through the existing gateway to the Ticket's durable Employee session and worker context. Chat completion without a proposal leaves `paired_work` unchanged. A real worker proposal changes status to `awaiting_approval`.

A paired Stage's proposal always parks for approval even when the Ticket's scope would auto-accept a worker-owned proposal. Approval advances normally, then the next Stage's effective ownership supplies the resting status.

## API and CLI

- Add `PUT /api/tickets/{ticket_id}/stage-ownership/{stage}` with body `{ "ownership_mode": "worker" | "user" | "paired" | null }`. Null clears the override. The route is direct-write only, validates the Stage against the Ticket's Worker type, rejects terminal Stages, uses the canonical writer, commits before wake, and returns Ticket detail.
- Add `panels ticket ownership <id> --stage <stage> --mode worker|user|paired|default`; `default` sends null.
- Ticket PATCH accepts `execution_route`, not `implementer`; `execution_route` remains direct-write-only, so an attributed worker cannot change its own route. Serialized Ticket detail and copy text use execution-route vocabulary only.
- Existing Take over/Release endpoints remain but become current-Stage ownership override operations.

## Frontend

Preserve the Ticket layout and interactions.

- In the existing Ticket facts row, replace the implementer pill with **execution route** and add a current-Stage **owner** pill (`default`, `worker`, `user`, `paired`). Selecting `default` clears the override; explicit modes set it.
- Display current default/effective ownership from the Ticket/manifest contract; do not reconstruct backend semantics in the client.
- Show `paired_work` as **paired work** in Ticket status, Workspace status filter/byline StageMark treatment, Review projection where applicable, Sprint in-progress derivation, and shared lifecycle/status labels.
- Keep Take over/Release in its existing place and derive its label from whether the current Stage has an explicit `user` override.
- Change only the visible at-cap option label from **propose** to **Continue**. Continue still sends `propose`.
- Use existing keyed invalidation; any new event kind must satisfy the event/resource completeness test.

## Skills and docs

- `panels-worker` owns the shared meaning of Worker/User/Paired, including paired Ticket Chat and Chief reconciliation.
- `panels-worker-new-worker` explicitly requires every new non-terminal Stage to declare a default ownership mode and explains how to choose it; specialist skills do not redefine lifecycle mechanics.
- Update live docs for Worker types, Ticket scope/control, runtime eligibility, Chat, CLI, and execution routes. Delete transition-hook/Khushal-implementer claims rather than preserving compatibility prose.
- Record the ownership/scope/execution-route judgment in `decisions.md` and update `PROGRESS.md` every integration cycle.

## Required evidence

Use vertical RED -> GREEN slices. Required focused coverage includes:

- registry validation and manifest for mixed-mode probe/new-worker definitions;
- v21 migration, including active statuses and every implementer mapping;
- override set/clear validation, events, Take over/Release, and current/default/effective serialization;
- all canonical Stage-changing paths and worker-run settlement deriving the next resting status;
- automatic eligibility for all three modes plus unchanged ceiling/Stop/Continue behavior;
- paired below-ceiling proposal parks, ordinary same-session Chat remains paired without proposal, and proposal moves to awaiting approval;
- Chief create/reconcile preserves explicit Stop and defaults to Continue while deriving the next Stage owner;
- execution-route API/CLI/prompt/UI replacement with no live `implementer` contract left;
- Ticket, Review, Workspace, Sprint rollup, StageMark, status filters, and mobile/desktop Playwright coverage;
- coding, shipped `new_worker`, and the novel probe fixture;
- worker skill provisioning/content and current docs.

After focused slices: read-only Codex review of the complete diff, written disposition of every finding, then exactly one final `./verify` run. A clean final run is the completeness claim.
