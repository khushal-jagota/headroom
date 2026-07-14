# AD06 contract lock

The orchestrator generated this lock after the corrected implementation plan passed a fresh independent
review. Implementation must consume these names and boundaries exactly. It may replace the AD05-era human
stream/chunk internals inside the reviewed allowlist, but it must not retain a compatibility owner, method,
callback, chunk, launcher, partial eligibility rule, or second human admission path.

This lock supersedes AD05 only where AD05 deliberately left deeper turn concentration to AD06. It also
supersedes only AD03's seven-factor enumeration by adding one eighth factor to AD03's same sole eligibility
function; AD03's discovery/claim identity and claim-writer boundary remain exact.

## Owner and admitted execution value

`src/planner/chat/service.py` owns exactly this private frozen value and public lifecycle class:

```python
@dataclass(frozen=True)
class _AdmittedHumanChatTurn:
    entity_id: str
    entity_kind: ChattableEntityKind
    turn_id: str
    expected_session_key: str | None
    model_text: str
    mode: Literal["message", "command"]
    image_paths: tuple[Path, ...]
    gateway: GatewayAdapter
    force_fresh_session: bool


class ChatTurnLifecycle:
    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        gateway_provider: Callable[[], GatewayAdapter],
        now: Callable[[], int],
        db_path: str | Path,
    ) -> None: ...

    def start_human_turn(self, entity_id: str, request: ChatTurnRequest) -> ChatTurn: ...
    def pause_active_turn(self, entity_id: str) -> ChatTurn: ...
```

`_execute_turn(self, admitted: _AdmittedHumanChatTurn) -> None` accepts only that value. Effective session
identity and the one-use force-pending state are execution locals, not extra fields. The one human daemon
launcher is private to this class. Top-level `start_human_turn`, `pause_turn`, `_run_human_turn`,
`_reject_if_ticket_worker_running`, and `_unix_now` are deleted, not forwarded.

`start_human_turn` owns only human admission and execution. `pause_active_turn` deliberately controls the
product-visible active Chat turn regardless of human or worker origin. It interrupts and settles Chat while
leaving Ticket status, Employee delivery, and Employee settlement untouched. It emits no automatic-
eligibility wake.

The server composes one lifecycle with a dynamic provider equivalent to
`lambda: app.state.adapters.gateway`. Each admitted execution snapshots one gateway; Pause resolves the
current gateway when called. The generic and hosted Chief routes marshal one `ChatTurnRequest` into this
owner, preserving stripped generic text and original Chief whitespace.

## Closed human transport

`ChattableEntityKind` is exactly
`Literal["ticket", "day", "agent_chat_session"]`. `ChatTurnRequest` keeps text, mode, and ordered image
references. `ChatStreamChunk` is deleted. Human gateway observations are exactly:

```python
@dataclass(frozen=True)
class HumanChatOutputDelta:
    text: str


@dataclass(frozen=True)
class HumanChatCompletion:
    text: str
    role: Literal["assistant", "system"]


HumanChatObservation: TypeAlias = (
    ChatActivityObservation | HumanChatOutputDelta | HumanChatCompletion
)
```

The one human transport capability is:

```python
HumanSessionKeyBinder: TypeAlias = Callable[[str], str]

def run_human_turn(
    self,
    session_key: str | None,
    entity_id: str,
    text: str,
    mode: str,
    bind_session_key: HumanSessionKeyBinder,
    image_paths: tuple[Path, ...] = (),
) -> Iterator[HumanChatObservation]: ...
```

Echo, Offline, Real, entity routing, SharedGateway, and every human-delivery test double implement only this
new contract. `GatewayAdapter.stream`, optional `on_session_key`, yielded session observations, completion
session keys, old `_stream_*` human helper names, and compatibility aliases are deleted. Employee
`run_ticket_step`, its callback, and `GatewayChild.send` are unrelated and remain.

## Atomic admission and complete automatic eligibility

Managed-image validation still completes before visible turn creation. Day and Chief retain current
materialization order. For a Ticket, the final `agent_running_step` check and human `chat_turns` plus visible
message/start-event creation occur in one `BEGIN IMMEDIATE` transaction through a non-owning
`start_turn_in_transaction` primitive. The worker-facing `chat_data.start_turn` keeps its transaction wrapper.

The sole `is_eligible_for_automatic_employee_step` function gains exactly one eighth factor: no running
Panels Chat turn exists for the Ticket. Discovery and final claim continue calling that same complete
function. `claim_automatic_employee_step` gains no Chat query, guard, parameter, partial condition, or
runtime import. Whichever write transaction commits first excludes the other before its durable and gateway
effects.

Chat completion, error, and Pause do not ring the eligibility wake. SQLite plus the canonical periodic
`AutomaticEmployeeStepDiscoveryLoop` timer observe the Ticket after Chat settlement.

## Causal session binding

`chat.data.bind_human_turn_session` owns one transaction and returns the effective durable key:

```python
def bind_human_turn_session(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_kind: ChattableEntityKind,
    entity_id: str,
    expected_session_key: str | None,
    candidate_session_key: str,
    force_fresh_session: bool,
    now: int,
) -> str: ...
```

It validates the matching still-running human turn, reads the entity key, atomically persists or adopts the
winner, attaches that same effective key to the turn, emits only the existing changed-key/changed-turn
events when their values change, commits, and returns the same key. Ordinary compare-and-set behavior adopts
a concurrent database winner. Repeated same-key reports are no-ops; genuine later rotation persists once;
a late callback cannot write after settlement.

Only exact command-mode `/new` sets `force_fresh_session`. Its first successful binding replaces any current
entity key with the freshly created candidate, attaches it, and returns it as the actual live Hermes session
used. The flag clears only after that success. Every later report uses ordinary compare-and-set behavior, and
no HTTP/request/gateway surface can force a key. `/new` with arguments is ordinary command dispatch.

SharedGateway binds every created, resumed, recovered, rotated, or reused live human session before image,
prompt, slash, alias, or derived prompt submission. If the binder returns a different key, SharedGateway
resumes/reuses that winner and writes on its live handle. Dormant recovery may retry only after
`LiveSessionDormant` proves no write was admitted; accepted or uncertain writes are never retried. Literal
`/new` creates one fresh candidate and the next ordinary message reuses its live handle without resume,
recreation, or replay.

## One Chat settlement door

`chat.data.settle_chat_turn` is the idempotent terminal writer for either visible-turn origin:

```python
def settle_chat_turn(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    status: Literal["complete", "errored", "interrupted"],
    reply_text: str,
    output_role: Literal["assistant", "system"],
    error: str | None,
    now: int,
) -> ChatTurn: ...
```

The first settlement clears activity, preserves final or partial output, appends at most one visible
assistant/system message, and emits exactly one existing `chat_turn_finished` event. Error settlement has a
nonempty error and no assistant/system message. Later completion, Pause, failure, exhaustion, unsupported
observation, or callback returns the first durable result without overwriting or duplicating it.

Worker-origin Pause preserves partial output, leaves `ticket_status=agent_running_step`, emits no eligibility
wake, and leaves eventual Ticket outcome with `EmployeeStepRunner`. Ordinary worker completion/failure may
continue using worker-facing low-level writers.

## Preservation, deletion, and boundary

Public HTTP/JSON, visible roles/text, activities, images, commands, event kinds/payloads, errors, and session
continuity remain materially unchanged. Tests must prove both race orders, all eight eligibility factors at
discovery and claim, worker-origin Pause, first-wins settlement, causal actual-session use at initial and
dormant seams, forced `/new`, ordinary lost-first-write adoption, image ordering/cleanup, and Employee
separation. The complete existing image and live-Chat browser suites remain preservation contracts.

History and state keep their current silent Hermes-history fallback and dedicated history-key persistence;
AD09 owns their later separation. AD07 Managed Markdown and AD08 resource-catalogue work remain excluded.
There is no schema/status, queue, scheduler, registry, recovery journal, timeout/retry policy, frontend, or
visible redesign.

Implementation may change only the paths in the reviewed plan's bounded allowlist. In particular,
`src/planner/tickets/data.py`, `src/planner/runtime/employee_step_runner.py`, session-manager internals,
database migrations, frontend source, generated assets, and e2e tests are not edit targets. Any need to
change this skeleton or widen the allowlist returns to the orchestrator before work continues.
