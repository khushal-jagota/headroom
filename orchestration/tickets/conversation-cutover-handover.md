# Conversation cutover — handover

Branch `simplify/conversation-cutover`, worktree
`planning-v2-worktrees/conversation-cutover`, merge base `ed72442f`.

This is written for someone who was not here. It assumes you know Panels but nothing
about this piece of work.

## What the cutover is

Two conversation systems ran side by side. The old package `src/planner/conversation/`
served the ticket pane over a WebSocket. The new `src/planner/conversation2/` served a
dev route, and a deterministic in-memory stand-in satisfied the worker path. The old one
dies; `conversation2` is then renamed to `conversation` and the numeral goes.

**Where it has got to: the old package is mounted nowhere in production and nothing has
been deleted.** Every screen that shows a conversation is on the new system. The old
package still exists, still compiles, and is still reachable over four HTTP surfaces
listed under "the enumeration" below. Deleting it is the remaining work.

## The standing rule before anything is deleted

**Compiling is not the test.** The two screens that nearly got missed call an HTTP route.
A route that stops existing fails at runtime in front of the owner, not in a gate, and a
type check will never see it.

So: nothing is deleted until an enumeration of everything the old package **serves** —
every HTTP route, the WebSocket, every endpoint its screens and the CLI call — shows
nothing reaching for any of it. That enumeration is the green light, not a clean build.

The sweep grew twice during this work when someone looked harder. Do not treat the lists
below as closed.

## Done, commit by commit

Read newest first; they were made in the reverse of this order.

- `b0885d95` **An agent is a thing, not a link.** Renamed the new table to `agents` and
  made the row the agent rather than the link. Reset nulls the conversation and leaves
  the row.
- `fdaa9d38` **The Chief owns a conversation the way a Ticket does.** The Chief screen on
  the new pane, the two doors, the `agents` table, the migration.
- `2bb06db5` **A message somebody else sent is not yours.** A prompt from the loop is
  drawn in the system style rather than the reader's own with a label.
- `946cf30e` **A model keeps the efforts it said it takes.** Per-model reasoning efforts
  carried through the backend snapshot.
- `16bfa516` **The unit suite cannot start a real agent.** The spawn guard.
- `16308a9f` **Being live is one piece of code, not one per screen.** The binder
  extraction; dev route and ticket page both on it.
- `c4ebe83f` **A person can open a Ticket's conversation, and close it.** The two ticket
  routes.
- `5daa9e9e` **Where Hermes lives is a fact about the machine.**
  `conversation/hermes_backend_configuration.py` → `environments/hermes_home.py`.
- `b288f4fe` **The built app is the app that is here.** Rebuilt the committed `web/dist`,
  which the merge had left matching neither branch.
- `e9687da7` **A message can be aimed at a skill.** The `/` button.
- `24924ac7` **A worker's prompt goes into a real conversation.** The worker path swapped
  off the in-memory stand-in.

### Gates as at the last commit

`ruff check .` clean · `mypy src/ tests/typing/` clean, 187 files · `pytest tests/unit`
1784 passed, 17 skipped · `npm --prefix web run check` 337 files, 0 errors · all 19 web
suites exit 0 · the three e2e specs that touch what changed, 15 passed.

**Do not run `./verify`.** The programme reserves one clean full run for the settled tree.
A full e2e run was started mid-work and stopped deliberately at 45 minutes, because a
failure list gathered across a tree being changed costs more to triage than it saves.

## Things you will otherwise re-litigate

Every one of these was ruled. Do not reopen them without the owner.

- **A ticket with no conversation gets a start route, not an empty state.** The old pane
  started one on demand, so an empty state is a capability that existed and would be lost.
  Same reasoning for New: `reset_ticket_conversation()` existed with no route, so it got
  one.
- **The Chief gets exactly what a Ticket gets and nothing of its own.** If you find
  yourself writing something that exists only for the Chief, stop and ask. The Chief's
  configuration stays on disk under worker settings, exactly as a worker's does; only its
  live state went into the database. Moving its settings would make it *less* like a
  worker.
- **The `agents` table holds an agent and its current conversation and nothing else.** A
  table called `agents` invites a name, a status, a last-seen, a created-at. None have a
  use today. If you want one, say what needs it.
- **Deliberately gone, each a decision recorded in the new code with its reasoning — do
  not carry them back:**
  - Panels lending its filesystem and terminal to an agent. `hermes_acp.py` says why:
    hermes runs in the workspace folder with its own access.
  - Thinking as a stored row. It is a live tail frame, dropped at ingestion; `events.py`
    says so.
  - The per-employee ACP session id as an identity. The conversation id is the identity;
    the vendor session cursor is internal.
  - **The protocol-rejection and unsupported-content banners.** They reported that Panels
    could not understand what the agent said — diagnostics from when the ACP layer was
    new. They tell a reader nothing about their own work, and a genuine failure has its
    own path to the surface now. This is a deliberate removal.
  - **`deferInitialAttach`.** The old ticket pane took it so that opening a ticket whose
    configuration was still editable would not spin up an agent. The new system spawns
    nothing until a message is sent, so there is no attach to defer. `acp-components.test.mjs`
    asserts the prop is **absent**, deliberately, so the removal carries its reason forward
    instead of being silently re-added.
- **The fourth backend key goes.** `ConversationBackendKey` is a closed set of three on
  purpose — the contract states a per-backend fact about steering. A worker type declaring
  a fourth key is not merely untested, it is unrepresentable: it could never start a
  conversation. The probe fixture keeps existing and points at a real key.
- **`employee_session_id` is renamed to `conversation_id` in the migration.** It holds a
  conversation id and says otherwise.
- **`ConversationTestOptions` needs no replacement.** See "the e2e stand-in" below.

## Known, named losses

These are accepted for the window between deletion and the package that follows. Record
them where the deletion is recorded; do not build a content-block path inside the cutover.

The new record carries a **string** where the old one carried **content blocks**. Four
things follow, all with the same cause:

1. **Images.** The old composer did file picker, drag and drop, paste, thumbnails, a
   remove control, a count badge and a drop overlay. `SendBody.text` is a `str` end to end.
2. **Links in the human's own message.** The old pane put every content block through the
   shared markdown renderer, which turns links and images into inline previews.
   `ConversationTranscript.svelte` renders `item.row.text` directly for a prompt row, so a
   pasted link stays literal. The agent's message still goes through `MarkdownBlock` — the
   mechanism survives untouched.
3. **`resource_link` blocks** — the agent handing back a file it produced, drawn as an
   embedded preview.
4. **Audio.**

These belong to the package the owner has scoped as **"the record carries content, not
only text"**, which lands immediately after the cutover and before the conversation's
three-state redesign.

One more thing worth knowing if images are picked up: `web/src/lib/acp/pendingConversationImages.js`
and its suite `acp-images.test.mjs` are the accept/reject, thumbnail, remove and revoke
logic, already written and tested. The browser half is a move, not a rewrite. Only the
send path is missing.

## The remaining work

### 1. The backend key and configuration change — ONE piece

This is the next thing and it is bigger than it looks. It was attempted and reverted
rather than left half-converted.

"Validate against `ConversationBackendKey`" is the **last line** of this work, not the
whole of it. Changing the probe fixture onto a real key and dropping the fourth
registration produces **twelve failures across five files**, and the shape of them is the
point:

- `tests/unit/test_ticket_edit_api.py` — eight failures. This is the employee-configuration
  surface.
- `tests/unit/test_worker_type_manifest_endpoint.py` — the manifest serves the catalog's
  key list.
- `tests/unit/test_seed.py` — the seed importer validates through the catalog.
- `tests/unit/test_chief_external_work.py` — the external-work backend default.
- `tests/unit/test_worker_type_registry.py` — the probe's default backend.

So the fourth key, the catalog's death, the employee-configuration repoint and the
manifest endpoint are one change. Done separately you fix the same tests twice.

**What dies:** `conversation/backend_catalog.py`. Its only surviving job is
`require_registered(backend_key)` — membership in a closed set the contract already ships
as `ConversationBackendKey`. Everything else it holds is old ACP child factories and
configuration adapters. Eight call sites: `tickets/data.py` (×4), `tickets/api.py` (×2),
`seed/importer.py`, `worker_settings/service.py`, `worker_types/registry.py`.

**Also dies:** `conversation/employee_configuration.py`. Not shared infrastructure — old
first-session machinery, read out of `app.state.conversation`.

**What replaces it:** `/api/conversation2/backends` already serves nearly the same shape.
The mapping is close to one-for-one:

| old catalog | new snapshot |
|---|---|
| `models` | `available_models` |
| `native_model` | `default_model_id` |
| `reasoning_efforts` | `reasoning_effort_options` |
| `native_reasoning_effort` | `default_reasoning_effort` |
| `reasoning_supported` | the options list is non-empty |
| efforts for a `candidate_model` | `BackendModel.reasoning_effort_options` |

That last row was the one genuine gap and it is **already closed** (`946cf30e`). The
browser was written for it all along — `effortOptionsFor` in `composer.ts` reads
`model.reasoning_effort_options` and falls back to the backend-wide list.

**Two screens repoint:** `WorkerConfigurationSetup.svelte` (backend/model/effort on a
ticket) and `ManagedLaunchDefaults.svelte` (the same as a managed default in worker
settings).

### 2. The reply dot

Three things die together:

- `src/planner/tickets/conversation_projection.py` (201 lines)
- the `agent_reply_state` field, composed in `tickets/views.py` from
  `tickets/logic/workspace_signals.py`
- `POST /api/tickets/{id}/acknowledge-completed-response`, called by `TicketRoute` on mount

The browser half **already exists and is unwired**: `web/src/lib/replyWatermark.ts`,
keyed by conversation id, with a comment naming the seam. A board row gains its
conversation's latest turn-ended line number; the row computes
`reply_waiting = latestTurnEndedLine > readReplyWatermark(conversation_id)`; the ticket
pane writes the watermark when the user views the conversation.

The only new server-side piece is **one storage read**: the latest turn-ended sequence per
conversation. `ConversationStore` today has `read_events_after`, `read_conversation` and
`has_delivered_prompt`, and nothing that answers it.

### 3. Deletion

Only when the enumeration is green.

**Python:** 30 of the 32 modules in `src/planner/conversation/`. (`hermes_home` already
moved out; `backend_catalog` and `employee_configuration` die with item 1.)

**Frontend — the trap.** Shared leaves hide inside the doomed package. Of the ten files in
`web/src/lib/acp/`:

- **`stepIcons.ts` SURVIVES.** The new `ToolCallRow.svelte` imports it, and
  `conversation2-pane.test.mjs` whitelists it as the one allowed leaf. Move it into the
  conversation lib.
- The other nine die, along with all ten components in `web/src/components/acp/` and
  `web/src/components/AcpConversation.svelte`.

> **`web/src/lib/acp/filePreview.ts` is a four-line re-export of `web/src/lib/filePreview.ts`.**
> Only the wrapper dies. The real one is used by the file-preview screen and
> `managedMarkdown.ts` and has nothing to do with conversations. Deleting the wrong one
> here is silent and nasty.

**Node suites that die:** `acp-contracts`, `acp-browser-state`, `acp-browser-conformance`,
`acp-browser-components`, `acp-components`, `acp-images`, `acp-production-mount`,
`conversation-controller`. (`acp-components` and `acp-production-mount` currently carry
assertions about the NEW pane that were repointed during the cutover — move those
somewhere that survives rather than losing them.)

**Then rename** `conversation2` → `conversation`, and `web/src/lib/conversation2` and
`web/src/components/conversation2` likewise. Nothing keeps a name referring to the thing
that no longer exists — including the `data-conversation2-*` attributes and
`/api/conversation2`.

### 4. The migration

**Three tables die:**

- `employee_step_runs`, with its `idx_employee_step_runs_one_running` index
- `employee_conversations` — the old binding repository
- `employee_configuration_catalog_cache` — the old catalog cache

**Ticket columns that STAY, and why:** `employee_backend`, `employee_launch_model` and
`employee_launch_reasoning_effort` are not frozen. The new code reads them as the launch
defaults and the last-chosen values. Do not drop them.

**One rename, ruled in:** `tickets.employee_session_id` → `tickets.conversation_id`. It
holds a conversation id. Doing it later means a second migration for nothing.

Note `tests/unit/test_db.py` pins `CURRENT_SCHEMA_OBJECT_COUNT` (25 as of `b0885d95`) and
three test files pin `HEAD_REVISION`. Both are deliberate guards — update them with the
change, do not weaken them.

### 5. Still ruled in, still unsized

All three are **carry across**, and all three need the record to carry something it does
not carry today. That makes them the same class of work as images, not the same class as
the `/` button.

**The lead wants each sized against the adapters before anyone starts.** If one turns out
to reach into the backend adapters, it wants ruling alongside images rather than slipping
in.

- **(a) Token usage and cost.** The old pane's header showed used/size and a
  currency-formatted cost. There is no usage concept anywhere in the new record. The
  owner's words: "let's just keep it, we don't know where we want to put it yet" — so get
  the numbers into the record and show them somewhere defensible for now. Do not let it
  die because its final home is undecided.
- **(b) Cancelling a single queued message.** The old composer listed the queue and could
  cancel a named message. The new tray shows `heldPromptCount` — a number, no list, no
  cancel — and the contract has no operation for it. The owner uses the tray.
- **(c) The compaction marker.** The *ability* survives untouched: Panels never initiated
  compaction, the backends do it themselves (hermes reports compacted boundaries, codex
  sends context-compaction items). What does not survive is the record **noting** it, and
  that matters more than it sounds — without a marker a reader sees a transcript whose
  earlier context has silently gone with nothing saying why.

## The enumeration, as it stands

What the old package serves, and who reaches for it. **One of five has no caller.**

| Surface | Callers |
|---|---|
| WebSocket `/api/conversation` | **none** — the only client was `lib/acp`'s transport, mounted nowhere. Nothing in `web/src` outside `lib/acp` constructs a WebSocket. |
| `GET /api/employee-configuration-catalog` | `WorkerConfigurationSetup.svelte`, `ManagedLaunchDefaults.svelte` |
| `PUT /api/tickets/{id}/employee-configuration` | `TicketRoute.svelte`, **and the CLI at `cli/main.py:640`** |
| `GET /api/worker-types` | the query catalogue — it serves `employee_backends` from the old catalog |
| `POST /api/tickets/{id}/acknowledge-completed-response` | `TicketRoute` on mount — dies with the reply dot, not the package |

Plus `EmployeeBackendCatalog.require_registered` at eight internal call sites with no route
of their own.

The CLI caller is the one a screens-only sweep misses. Sweep by route.

## How things are shaped now

### The conversation system in the app

`create_app` builds the conversation runtime at the top of the lifespan, **before** the
readiness loop that sends into it. `app.state.conversation_system` is
`conversation2.system` — the same object the browser's conversation routes use. One system,
not two.

`create_app` takes `conversation_system_for_test`, refused outside test mode exactly as
`conversation_test_options` is. Four unit files pass an `InMemoryConversationSystem()`.
That is now the only way the stand-in is ever composed.

The in-memory system **stays**. It is the reference implementation of the contract, it
lives in the surviving package, and loop/readiness/board tests use it directly without an
app.

### The spawn guard

`tests/unit/conftest.py` replaces the production backend child factories for the whole
unit suite, so a test that composes an app and drives a conversation without saying which
conversation system it wants **raises** instead of starting a real agent. Two tests were
reaching real agents while passing green before this.

Its message names the backend and conversation and shows the exact `create_app` call to
fix it. There is **no exclusion list** and none is possible to get wrong: the four opt-in
`PANELS_REAL_*` files build their adapters directly and never touch the application's
composition, so the guard structurally cannot reach them.

### The binder

`web/src/components/conversation2/LiveConversation.svelte` is what makes a conversation
live: it opens one by id, replays the rows after the one it holds and keeps going, keeps
the messages this browser has sent that the record has not caught up with, and turns send,
stop, answer and New into calls. Three callers: the dev route, `TicketRoute`, and
`ChiefConversation`.

**Two traps found the hard way, both of which passed `svelte-check`:**

1. **Holding an id is not the same as a conversation existing.** A ticket names its
   conversation before anyone opens the page, and the dev page puts an id in the address
   before anything is started under it. What decides whether the first message must start
   one is **the record answering**, never the id being there. Getting this wrong sent the
   first message to a conversation that had never been created.
2. **A snippet takes the name it is declared with.** Passing an `emptyState` prop through a
   wrapper snippet also called `emptyState` shadows the prop and renders itself —
   infinite recursion, and the entire pane silently failed to render. The local snippet is
   named `beforeThereIsAConversation` for this reason.

### The e2e stand-in

`ConversationTestOptions` needs no replacement. The repository already contains the better
pattern and it is proven: `tests/e2e/test_dev_conversation_pane.py` stands in for an agent
by writing rows straight into the record with the real `ConversationStore` and reading them
back over HTTP the way the browser reads any other row. Ten browser tests run on it. No
scripted backend, no fake child, no injection into `create_app`.

If something genuinely needs a live turn rather than a transcript, the seam is a test-mode
`backend_child_factories` parameter on `create_app` mirroring `conversation_system_for_test`.
**Do not add it until something needs it.**

### The Chief

`agents` table, `agent_key` primary key, nullable `conversation_id`. Routes
`GET/POST /api/chief/conversation` and `POST /api/chief/conversation/reset`, both human-only,
keyed by `CHIEF_SETTINGS_KEY` from `worker_settings/service.py` — no new vocabulary.
`start_agent_conversation` / `reset_agent_conversation` / `read_agent_conversation` in
`runtime/conversation_start.py` mirror the ticket trio exactly.

The Chief appears in two places — its own screen and the Workspace desk — and both go
through `web/src/components/ChiefConversation.svelte`, so they are one conversation. They
were two separate mounts before, which is two things that can disagree.

## A flaky test

`tests/unit/test_worker_step_readiness_loop.py::test_a_ticket_already_in_flight_is_not_scheduled_twice`
failed once in a full run and passed both in isolation and on the next full run. Not
chased. It is about scheduling and concurrency, so it is the kind of flake that is
sometimes real. Recorded so it does not surprise the final verify.
