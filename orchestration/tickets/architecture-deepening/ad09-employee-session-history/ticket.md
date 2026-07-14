# AD09 — Separate Employee session history from Panels Chat

## Outcome

Make the two existing truths explicit and impossible to confuse:

- **Panels Chat** is only the durable product-visible messages and live turn state stored by Panels.
- **Employee session history** is the explicit Hermes record of what the Ticket's employee actually
  received and produced in its durable session.

`GET /api/chat/{entity_id}/state` must read Panels-owned rows only. It never calls Hermes, never fills an
empty visible transcript from Employee history, and never persists a rotated Employee session as a side
effect. Employee history remains available through one explicitly named Ticket-only interface. A caller has
to ask for it deliberately.

This is also the final vocabulary separation. A Ticket owns an `employee_session_id`, not a
`chat_session_key`. Human Ticket Chat and automatic/revision Employee steps still deliver through that same
durable Employee conversation; Panels Chat rows remain the visible mirror, not model context. Day and
top-level-agent Chat sessions may keep their honest Chat-specific storage because they are not Ticket
employee identity.

The change lands last in one isolated commit because restart, resume, rotation, history inspection, human
binding, Employee claim, `/new`, and migration behavior all touch the same durable identity.

## Binding decisions and contracts

- `CONTEXT.md`: Panels Chat and Employee session history are distinct named concepts.
- `D-panels-chat-and-employee-history`: Employee history is authoritative for what Hermes received and
  produced, useful for continuity, inspection, and recovery, but never silently merged into Panels Chat.
- `D-live-chat-state`: Panels owns the visible transcript and active turn; navigation and restart read that
  durable product state rather than reconstructing it from Hermes.
- `D-panels-chat-not-worker-context`: Panels rows do not deliver anything to the employee. Actual delivery
  remains through the Hermes session or the Employee prompt.
- `D-worker-context-subsystem`: the actual submitted Employee prompt may contain internal context not present
  in the visible Panels line. Once history is explicit rather than a visible fallback, it must report the
  actual Employee-session record rather than disguising it as the Panels-visible prompt.
- AD06's canonical human-turn and atomic admission/session-binding behavior remains one owner.
- AD08's temporary Ticket-plus-Chat `chat_session_created` dependency must be retired consistently after
  the Ticket Employee identity is named separately.
- The owner's Worker-type ruling remains exact: every live Ticket creation requires an explicit Worker type.
  A migration may rewrite historical rows and events, including assigning `coding` only to an old Ticket
  shape that predates stored Worker type. No live Python, HTTP, SQLite, CLI, frontend, or fallback default is
  permitted.

## Current shallow boundary to replace

The current implementation conflates three things under “Chat history”:

1. `tickets.chat_session_key` is the durable Hermes identity used by Employee steps, worker self-resolution,
   revision delivery, and human Ticket Chat.
2. `GET /api/chat/{entity_id}/history` reads Hermes for Tickets, Days, and top-level agents through a
   Chat-owned contract.
3. `GET /api/chat/{entity_id}/state` silently calls that history endpoint internally when Panels has no
   `chat_messages`, converts the Hermes rows into synthetic negative-id Panels messages, and can persist a
   rotated key during an ordinary visible-state read.

That fallback makes an empty Panels transcript mean “whatever Hermes happened to return”. It can expose
Employee prompts, hidden revision guidance, worker context, system/tool content, and delivery details as if
they were intentionally product-visible Chat. It also gives a read of Panels state write authority over the
Ticket's durable Employee identity.

The Ticket field and event vocabulary are equally shallow: automatic Employee claims and human Chat binding
both write `chat_session_key` and emit `chat_session_created`, so frontend invalidation has to pretend a
transport identity change is both Ticket data and visible Chat data.

## Exact domain split

### Ticket Employee identity

Rename the Ticket-owned durable identity everywhere to `employee_session_id`:

- canonical SQLite column and migration shape;
- `Ticket` contract and Ticket JSON response;
- Employee-step runner variables and persistence callbacks;
- Ticket lookup used by `panels worker my-ticket`;
- revision admission and all status-settlement writers that carry the durable id;
- human Ticket Chat resolution and binding;
- seed/import conversion of the historical `Chat ID:` value;
- tests, docs, comments, and skills that describe Ticket employee identity.

The Hermes transport may still call its protocol value a session key internally. At the Ticket/domain/API
boundary it is an Employee session id. Do not keep a `chat_session_key` Ticket property, JSON alias, SQL
column, helper alias, or compatibility route.

One canonical Ticket data writer owns an Employee-session-id transition and its event. Human binding,
Employee claim/rotation, explicit history rotation, `/new`, and settlement helpers must use that writer
inside their required transaction/admission rules rather than issuing independent Ticket updates.

Introduce `employee_session_changed` for Ticket Employee-identity changes, with a payload named
`employee_session_id`. Live Ticket paths stop emitting `chat_session_created`. Day and top-level-agent Chat
may continue emitting `chat_session_created` with Chat session-key payloads because that name is true there.

### Panels Chat state

Panels Chat state is constructed only from `chat_messages`, the one active `chat_turn`, and its display-safe
activity entries. Delete the Hermes-history fallback and every synthetic negative-id message path. An empty
Panels transcript stays empty even when the Ticket has a long Employee session history.

The public `ChatState` response must not expose the Ticket's Employee session id as top-level visible state.
The reviewed plan must inventory whether any active-turn session field is required solely to preserve the
current Pause enablement timing; if so, expose only the smallest honest control capability rather than the
durable Employee identity. Internal turn/session binding and server-side Pause resolution remain exact.

Reading Chat state is read-only: no gateway call, session resume, Ticket update, event, message insert, or
turn insert. Existing Panels rows survive restart and remain the sole visible transcript.

### Explicit Employee session history

Replace the generic Chat history route with one Ticket-only interface named for the concept, for example
`GET /api/tickets/{ticket_id}/employee-session-history`; the delegated plan must freeze the exact route,
contract types, service owner, and gateway method names. Delete `/api/chat/{entity_id}/history` rather than
keeping an alias. Days and top-level agents do not acquire an “Employee” history endpoint.

The response contains the actual normalized Hermes messages and the effective `employee_session_id`. It is
direct-only under the same human authorization rule as the old history read. With no Employee session id it
returns an empty history without spawning/resuming Hermes or writing state. With an id it resumes/reads that
specific durable Employee session. Gateway/offline errors remain honest and do not modify the Ticket.

If Hermes reports a rotated effective id, the service persists it through the one Ticket Employee-session
writer and emits one `employee_session_changed` event. A history read never creates a brand-new Employee
session merely to produce an empty result. Concurrent rotation/persistence behavior must be frozen in the
plan and tested; it must not overwrite a newer durable winner with an older history result.

The explicit history is authoritative for what the employee actually received and produced. Do not run its
user messages through the old Panels-visible prompt stripping used to make silent fallback look friendly.
Panels keeps its own intentionally visible text in `chat_messages`; Employee history may therefore show
internal pending context, revision guidance, system/tool messages, or other real Hermes content that Panels
does not display. Native image delivery remains whatever Hermes truthfully exposes; do not synthesize a
Panels Markdown attachment into history.

## Migration and historical rewrite

Advance the canonical schema and fold this rename into the existing terminal, lock-held Ticket migration
rather than adding a live fallback or a post-read patch:

- a current v19 `tickets.chat_session_key` value is copied byte-for-byte to
  `tickets.employee_session_id`;
- every older recognized Ticket shape still reaches the one canonical final table under the existing
  `BEGIN IMMEDIATE`/foreign-key envelope;
- a canonical `employee_session_id` is preserved exactly on repeated opens;
- NULL remains NULL and the new column has no default;
- historical Ticket `chat_session_created` events are rewritten to `employee_session_changed`, renaming
  `session_key` to `employee_session_id` without changing event order or timestamps;
- Day and top-level-agent `chat_session_created` events remain unchanged; and
- migration failure rolls back table and event changes together.

This is where old stored data should be rewritten. Do not carry both columns, both event kinds for Tickets,
runtime `COALESCE`, a read fallback, or a compatibility view. The migration may continue its already-
sanctioned historical-only Worker-type classification; the live no-default rule remains unchanged.

## Restart, rotation, and delivery preservation

The separation must preserve these behaviors:

- an automatic Employee step creates or resumes the stored Employee session before prompt submission, so
  `panels worker my-ticket` resolves the Ticket during the turn;
- a revision requires and resumes the existing Employee session and sends guidance through Hermes without
  making the hidden guidance a Panels message;
- human Ticket messages, commands, images, and exact `/new` atomically bind the candidate Employee session
  to the running Panels turn before Hermes receives input;
- concurrent human candidates still adopt the durable winner, while exact `/new` forces its one new winner;
- rotated ids from Employee work, human work, and explicit history are persisted once with the new event;
- a process restart resumes the stored Employee session and explicit history remains readable;
- Panels Chat after restart shows exactly its durable Panels rows, whether Employee history is empty or
  non-empty; and
- Panels rows are never used as model context or as proof of Hermes delivery.

Do not redesign gateway ordering, turn ownership, interruption, observation normalization, Employee
eligibility, or settlement.

## Frontend/resource consequence

After AD08, update the Resource Catalogue rather than reintroducing a standalone mapper:

- `employee_session_changed` on a Ticket invalidates the matching Ticket detail only;
- the four Ticket Chat message/turn events invalidate the matching Panels Chat only;
- Ticket Employee-session changes do not invalidate Panels Chat, Board, current Sprint, or Review;
- Day/agent `chat_session_created` keeps only its real Chat dependency;
- Ticket detail uses `employee_session_id`, never `chat_session_key`; and
- Employee session history is an explicit ordinary read, not a cached Panels Chat resource unless the
  implementation inventory proves an actual production cached consumer. No new UI is required by this
  ticket.

The WebSocket completeness test must use representative entity prefixes that are valid for each event kind
and must prove no historical Ticket `chat_session_created` survives migration.

## Required RED-first acceptance

Add focused contract tests before rewiring production. Prove all of the following:

1. **No silent merge.** A Ticket with non-empty Hermes history and zero Panels messages returns an empty
   Panels `ChatState`; the state read makes zero gateway calls and zero writes/events. Panels rows, when
   present, return exactly and survive process restart.
2. **Explicit authority.** The Employee-history endpoint returns the actual Hermes messages only when
   explicitly requested, including an actual delivered prompt that differs from its Panels-visible mirror.
   It never inserts synthetic Panels messages.
3. **No-session and failure purity.** Missing Employee id returns empty without spawning Hermes. Offline,
   malformed, missing-Ticket, and agent-auth cases preserve the existing error envelope and write nothing.
4. **Rotation and races.** A rotated history id persists through the canonical writer and one new event; a
   stale concurrent history result cannot overwrite a newer winner. Repeated reads are idempotent.
5. **Delivery parity.** Automatic work, direct revision, human message/command/image, `/new`, Pause, busy
   outcomes, completion, error, and interruption retain exact Hermes delivery and Panels visible-state
   behavior while using `employee_session_id`.
6. **Worker self-resolution.** The real per-turn Hermes session id still resolves exactly one Ticket; a
   missing or old rotated id does not resolve another Ticket.
7. **Migration.** Every recognized historical Ticket schema, v19 values, NULLs, existing v20 values,
   Ticket-only event rewrites, mixed Ticket/Day/agent events, rollback, foreign keys, indexes, and repeated
   open are covered. No live or SQLite Worker-type default is introduced.
8. **Vocabulary deletion.** Scoped static tests find no Ticket `chat_session_key`, generic Chat history
   route/service/contract, synthetic legacy-state messages, or Ticket `chat_session_created` writer. Day and
   agent Chat-specific names are explicitly allowed.
9. **Resource precision.** Catalogue tests prove the new Ticket event affects Ticket only and Chat events
   affect Chat only, with no phantom or whole-screen invalidation.
10. **Browser preservation.** Ticket Chat remains visibly stable across send, remount, hard reload, active
    work, Pause, and WebSocket updates; an old Employee history that has no Panels rows never appears in the
    chat panel.

Do not weaken an existing assertion merely because it relied on the old conflation. Replace it with the
appropriate Panels-state or explicit Employee-history assertion.

## Documentation

Update `CONTEXT.md`, `docs/chat.md`, `docs/employee-runtime.md`, `docs/tickets-and-gates.md`,
`docs/systems.md`, and synchronized `docs/systems.html` wherever they still call Ticket Employee identity a
Chat key or imply that Hermes history populates the visible chat. Plainly document:

- Panels Chat is durable product-visible state;
- Employee session history is explicit, authoritative Hermes inspection;
- both human Ticket Chat and Employee steps deliver to the Ticket's `employee_session_id`;
- a restart resumes that durable Employee conversation;
- explicit history may contain content intentionally absent from Panels Chat; and
- a Panels row alone is never employee delivery.

Update worker/Chief skills and CLI help only where the old Ticket session vocabulary appears. Do not add a
visible history pane, toggle, source badge, or transcript-merging control.

## Explicit exclusions

- No Worker-type inference, fallback, default, mutability, or Stage change. Historical migration rewrite is
  allowed and required; live creation remains explicit.
- No new UI, Chat pane, history toggle, diff view, copy, CSS, route layout, or navigation.
- No new Employee-history database copy, message table, event log projection, replay log, or transcript
  synchronization job. Hermes remains the Employee history authority.
- No use of Panels `chat_messages`/`chat_turns` as Employee context.
- No change to gateway ordering, native busy/queued/steered behavior, turn ids, Pause semantics, activity
  safety, Automatic Employee-step eligibility, Review, Worker workflow, Managed Markdown, or mutation
  refresh policy.
- No compatibility alias for Ticket `chat_session_key`, `/api/chat/{id}/history`, old Ticket event payloads,
  or old gateway/history contract names.

## Plan requirements

The delegated plan must inventory every Ticket session column/property/JSON/event/helper, every human and
Employee session bind/persist path, every history caller and adapter method, state fallback, worker lookup,
seed/import surface, frontend type/resource dependency, test fixture, doc, skill, migration shape, and built
asset affected. It must freeze exact Python/HTTP/TypeScript declarations; the canonical writer and race
rules; migration recognition/event rewrite; response serialization; deletion set; RED order; restart and
rotation tests; focused commands; generated-asset expectations; and a bounded changed-path allowlist.

Any need to split the actual durable Employee conversation into two Hermes sessions, add a UI, retain an
alias, or change a backend/event contract beyond the separation above returns to the orchestrator before
implementation.
