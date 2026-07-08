# Spike 10 - V1 worker understanding

Explore-only snapshot of the old planning worker. No recommendation is made.

Read-only scope: v1 source roots were inspected at `~/.hermes/planning`,
`~/.hermes/hermes-agent`, `~/.hermes/skills`, `~/.hermes/memories`, and selected
persisted cron/session output. No files under the v1 planner or Hermes runtime
were modified.

## Source Key

- `[file: path:line]` means a static v1 file or code path.
- `[other-skill file: path:line]` means behavior inherited from another skill
  available to the worker, not from the worker skill alone.
- `[memory: path:line]` means explicit Hermes memory.
- `[embedded planner context: path:line]` means persisted planner feedback or
  cron/session context that shaped the worker but is not just the worker skill.
- `[runtime-injected file: path:line]` means Hermes runtime prompt/tool behavior
  injected by code, not by the v1 planner skill file.
- `[agent-interrogation]` would mean a live Hermes agent described itself. No
  live agent interrogation was performed in this spike because the normal
  `hermes chat` path creates/resumes durable session state under `~/.hermes`,
  which conflicts with the no-modification constraint for the v1 roots.

Embedded/non-obvious sources that materially shape the worker:

- Hermes memory compresses the planner model as tracker/overview/workspace,
  Workspace as Calls plus Concepts to Needs Shaping to Ready, conservative
  readiness, and small anchored markdown patches. `[memory:
  ~/.hermes/memories/MEMORY.md:1]` `[memory:
  ~/.hermes/memories/MEMORY.md:11]`
- Persisted cron/session context expands the worker's operating model: Workspace
  is a shaping surface by default, `Ready` execution requires explicit user
  choice, `In Progress` means active work can be continued, and agent
  report-back is not ticket completion. `[embedded planner context:
  ~/.hermes/cron/output/4e50489ebd16/2026-06-28_16-04-43.md:611]`
  `[embedded planner context:
  ~/.hermes/cron/output/4e50489ebd16/2026-06-28_16-04-43.md:612]`
  `[embedded planner context:
  ~/.hermes/cron/output/4e50489ebd16/2026-06-28_16-04-43.md:614]`
- Planner feedback says Concepts should be kept conservative, while Needs
  Shaping may do more autonomous exploration, add relevant detail, and refine
  real open questions without changing Success. `[embedded planner context:
  ~/.hermes/planning/feedback.md:2]` `[embedded planner context:
  ~/.hermes/planning/feedback.md:3]`
- The generic Hermes Kanban worker protocol is not in the planner worker skill:
  it is injected when `HERMES_KANBAN_TASK` is set, adding `KANBAN_GUIDANCE` and
  `kanban_*` tools. `[runtime-injected file:
  ~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/README.md:11]`
  `[runtime-injected file:
  ~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/README.md:15]`

## 1. What The Worker Is Doing

The v1 planning worker is primarily the Workspace detail-chat agent loaded with
`workspace-ticket-shaping`. It runs when the user chats from a selected
Workspace ticket or the `Necessary Calls` surface. The selected ticket/call is
implicit context, and the worker's default job is shaping the planning surface
toward clearer `Body`, `Success`, `Approach`, `Open questions`, `Readiness`,
and eventual `Mode`, not executing the underlying project work. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:2]` `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:17]` `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:20]`

The system around it is a markdown planning filesystem plus a small FastAPI
viewer. The planner's live day has `tracker.md`, `overview.md`, and
`workspace.md`: tracker is the committed plan/log, overview is judgement and
orientation, and workspace is the ticket-refinement plus active-work surface.
`[file: ~/.hermes/planning/CLAUDE.md:7]` `[file:
~/.hermes/planning/CLAUDE.md:17]` `[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:255]` `[other-skill
file: ~/.hermes/skills/productivity/daily-planning/SKILL.md:259]`

The viewer launches Workspace detail chats with the `workspace-ticket-shaping`
skill. The launch policy sets `HERMES_TUI_SKILLS=workspace-ticket-shaping` for
the `workspace-detail` surface, and wraps both `session.create` and
`session.resume` because Hermes rebuilds skills at session creation/resume.
`[file: ~/.hermes/planning/viewer/launch_policy.py:9]` `[file:
~/.hermes/planning/viewer/launch_policy.py:51]` `[file:
~/.hermes/planning/viewer/launch_policy.py:94]`

Each worker turn is wrapped by the viewer with planning file paths, selected
ticket or calls context, and the mutation policy. The wrapper tells Hermes that
the selected surface is the implicit subject, not to ask which item is being
discussed, and to treat Workspace chats as shaping by default. `[file:
~/.hermes/planning/viewer/server.py:1165]` `[file:
~/.hermes/planning/viewer/server.py:1175]` `[file:
~/.hermes/planning/viewer/server.py:1192]` `[file:
~/.hermes/planning/viewer/server.py:1216]`

The worker's normal successful output is a clearer selected ticket or a clearer
decision/follow-through from `Necessary Calls`. It can answer, investigate, or
edit the active planning markdown directly for obvious selected-surface shaping
changes, while preserving bigger moves for explicit approval. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:18]` `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:20]` `[file:
~/.hermes/planning/viewer/server.py:1192]`

For non-Ready tickets, the worker shapes rather than executes. For `Ready`
tickets, execution is allowed only when the user explicitly asks. For
`In Progress`, active work has already started, so the worker may continue,
inspect, resume, or coordinate within the ticket scope. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:49]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:260]` `[file:
~/.hermes/planning/viewer/server.py:1210]`

Completion is user-approved. An agent reporting that work appears complete does
not complete the Workspace ticket. When the user says the selected ticket is
done/approved, the workflow records a concise tracker `Done` outcome, updates
matching tracker status where needed, removes the ticket from Workspace, and
verifies parser/render state. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:34]` `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:40]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:327]`

There is a separate generic Hermes Kanban worker runtime. A Kanban worker is a
regular Hermes profile with task env vars, not a special process type; when
`HERMES_KANBAN_TASK` is set, Hermes injects lifecycle prompt guidance and
exposes the `kanban_*` tool surface. `[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/README.md:5]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/README.md:11]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/README.md:15]`

## 2. The Stages

`Necessary Calls` is outside the ticket tree. It holds cross-cutting decisions
or conversations that may affect several tickets, priorities, defaults, or the
whole day. The worker resolves or narrows the call, carries the decision into
the affected ticket/tracker/overview wording, and removes the call once the
decision is reflected unless the user wants a decision log. `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:7]`
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:51]`
`[file: ~/.hermes/planning/viewer/server.py:1198]` `[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:343]`

`Concepts` is the fuzzy ticket state. A Concept can be only a thesis, question,
or sparse body; no field is mandatory, and success may be unconfirmed. Work in
Concepts clarifies the desired result and success criteria, but the ticket stays
a Concept until promotion is explicitly chosen. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:87]`
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:18]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:322]`

`Needs Shaping` is the state where the desired result or success shape is known
enough to refine, but the path is still rough. The worker maps top-level routes,
compares options, surfaces risks and important questions, and narrows the
approach without executing the work. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:88]`
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:19]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:323]`

`Ready` is executable. A Ready ticket has enough structure for its declared
execution mode, including clear success and roughly how the work will get
there. `Mode` is required for Ready tickets, but there is no universal Ready
form beyond the title and mode; useful fields may include `Body`, `Success`,
`Approach`, `Verify`, watchouts, paths, constraints, examples, or return
expectations. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:89]`
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:23]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:364]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:369]`

`In Progress` is a readiness/work state for active work, not an execution mode.
Workspace chat from an In Progress ticket may help continue, inspect, resume,
or coordinate that work while staying scoped to the ticket unless the user
broadens scope. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:90]`
`[file: ~/.hermes/planning/viewer/server.py:1212]` `[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:325]`

`Done` is not a Workspace readiness state. Done happens only through the
user-approved completion workflow: tracker outcome first, matching tracker
status sync where needed, Workspace ticket removal, then parser/render
verification. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:36]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:327]`

Moving through the tree is deliberate. Filling in a field does not itself
promote a ticket; Concepts to Needs Shaping needs a short body, user-confirmed
or genuinely obvious success, and explicit promotion, while Needs Shaping to
Ready needs enough route clarity for the intended execution mode. `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:39]`
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-taxonomy-and-honest-tickets.md:50]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:274]`

The current file model is ticket-ID-first: canonical `workspace.md` uses
`## Necessary Calls` plus `## Tickets`; each work-tree ticket is a stable
record with `Ticket ID:`, `Readiness:`, `Mode:`, and optional `Chat ID:`. The UI
renders Concepts, Needs Shaping, Ready, and In Progress from the `Readiness:`
field. `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-ticket-id-first.md:7]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:270]` `[file:
~/.hermes/planning/viewer/server.py:527]` `[file:
~/.hermes/planning/viewer/server.py:618]`

The generic Kanban worker has its own lifecycle stages: orient with
`kanban_show`, work inside the assigned workspace, heartbeat during long work,
block on genuine ambiguity, complete with structured handoff, and create
follow-up tasks rather than doing newly discovered downstream work. This is
Hermes runtime behavior, separate from the planner Workspace readiness tree.
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/README.md:41]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:191]`

## 3. The Deeper Guidance

The worker is expected to ground itself before shaping or advising. It reads
the relevant sprint item, tracker, workspace, repo docs/source/tests/evals, or
branch state as narrowly as the ticket requires; if the user asks to get ground
truth first, that is a hard no-mutation gate until the worker reports truth and
gets explicit approval to change files. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:18]`

The selected ticket is already visible and injected into the prompt, so the
worker should not ask the user which ticket is being discussed or repeat the
ticket back as a confirmation ritual. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:17]`
`[file: ~/.hermes/planning/viewer/server.py:1178]`

`Success` is the main judgement bar. A good success condition says what makes
the work actually done, what evidence or output proves it, and what judgement
standard the rest of the ticket is built around. If there is not enough detail
to write a good Success, the ticket remains a Concept; a best-guess success bar
is still a Concept unless success is obvious from context or confirmed by the
user. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:50]`
`[memory: ~/.hermes/memories/MEMORY.md:1]`

Needs Shaping is for route/options exploration after success is known, not for
discovering what success means. The first move after promotion is to map the
top-level options and real questions, not to pretend the route is known.
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:50]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:275]`

`Approach` is the method or sequence for doing the ticket. For larger tickets,
it starts with the main route and then supporting detail; if only isolated
details exist and no main route is clear, the ticket is still being shaped.
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:361]`
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-taxonomy-and-honest-tickets.md:34]`

Open questions are readiness-reducing questions, not miniature execution. In
Needs Shaping, questions should reduce large ambiguity needed to start safely;
implementation/detail questions that are the work itself usually belong inside
execution, not as blockers to readiness. `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-taxonomy-and-honest-tickets.md:45]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:277]`

Discussion and decisions are separated. If the user asks to discuss, think
through options, or investigate, provisional findings stay in chat unless the
user asks for capture or the underlying decision clearly settles. Once a
structural decision settles, it belongs in durable ticket fields, and stale open
questions should be narrowed or removed. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:52]`
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:54]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:347]`

Routine selected-surface shaping edits are direct by default when obvious,
non-destructive, and scoped to the selected ticket or calls surface. The worker
asks first for readiness changes, removals of key information, major meaning
changes, cross-file edits beyond agreed follow-through, memory/skill/code
changes, sensitive changes, or explicit review requests. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:20]`
`[file: ~/.hermes/planning/viewer/server.py:1192]`

Readiness is underreported rather than inflated. Sparse or loose ideas stay in
Concepts; rich context does not automatically make a ticket Ready; and detailed
follow-up tickets are not automatically Ready merely because a source document
is thorough. `[memory: ~/.hermes/memories/MEMORY.md:1]` `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:59]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:282]`

`Mode` is attention/focus metadata, separate from readiness. It can be set early
when obvious, is required when Ready, and uses the closed vocabulary
`Autonomous`, `Supervised`, `Paired`, `Manual`, or `Campaign`. It is not tree
placement, executor type, or uncertainty notation. `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:43]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:288]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:350]`

Tickets are written for the user's understanding and steering, not as agent
forms. The worker keeps tickets lightweight, avoids duplicate detail, and adds
only constraints/context/known facts that change how the work should be done.
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:27]`
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:28]`
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:29]`

Non-Ready ticket chats do not mutate project files, code, skills, sprint files,
tracker files, or other planning surfaces unless the user explicitly approves
that broader action. Work is limited to the selected ticket's wording,
readiness, open questions, steering detail, or approved section movement.
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-tree-refinement.md:63]`
`[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-taxonomy-and-honest-tickets.md:75]`
`[file: ~/.hermes/planning/viewer/server.py:1215]`

Ready execution, when explicitly chosen, treats the ticket text as the execution
contract, checks dirty state first for code work, stays scoped to the named
implementation surface unless hooks are genuinely missing, verifies, and does
not mark planning done unless the user asks for the completion mutation.
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:327]` `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-ready-ui-ticket-execution.md:5]`

Markdown is the source of identity and state. `Ticket ID:` is ticket identity,
`Chat ID:` is Hermes chat/session identity, and both live in the active
`workspace.md`; sidecar state is explicitly not the primary identity mechanism.
`[file: ~/.hermes/planning/CLAUDE.md:49]` `[file:
~/.hermes/skills/productivity/daily-planning/references/workspace-taxonomy-and-honest-tickets.md:14]`
`[file:
~/.hermes/skills/productivity/planning-viewer/references/workspace-per-item-chat.md:13]`

Edits to planning markdown are expected to be small, exact, and anchored; the
worker reads/rereads the relevant section before patching and round-trips
workspace writes through the parser. `[memory:
~/.hermes/memories/MEMORY.md:11]` `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:79]`
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:80]`

The worker relies on other skills and references even when only
`workspace-ticket-shaping` is preloaded. The worker skill points to
`daily-planning`, `planning-viewer`, workspace tree refinement, taxonomy,
ticket-ID-first, per-item chat, and work-type boundaries; `daily-planning`
also carries planner-wide Workspace semantics and Ready execution/closeout
behavior. `[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:10]`
`[file:
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md:114]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:259]`
`[other-skill file:
~/.hermes/skills/productivity/daily-planning/SKILL.md:327]`

The generic Kanban worker's deeper guidance is embedded in the runtime prompt:
call `kanban_show` first, work only inside the assigned workspace, heartbeat
during long operations, block rather than guess on genuine ambiguity, complete
with a structured handoff, create follow-up tasks rather than doing them, do
not use `clarify`, and do not shell out to the Kanban CLI from an agent.
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:193]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:198]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:209]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:213]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:227]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:258]`

## 4. Tools And CLI The Worker Uses

For the v1 planner Workspace worker, the launch tools are Hermes TUI gateway
RPCs mediated by the planner viewer. The viewer creates or resumes a Hermes
session, gets a durable session key via `session.title`, writes that key as
`Chat ID:` before sending the first prompt, and submits the wrapped user message
with `prompt.submit`. `[file: ~/.hermes/planning/CLAUDE.md:55]` `[file:
~/.hermes/planning/viewer/server.py:1489]` `[file:
~/.hermes/planning/viewer/server.py:1520]` `[file:
~/.hermes/planning/viewer/server.py:1537]` `[file:
~/.hermes/planning/viewer/server.py:1574]` `[file:
~/.hermes/planning/viewer/server.py:1595]`

The viewer sets `TERMINAL_CWD` to `~/.hermes/planning`, so context-file
discovery loads the planner's `CLAUDE.md`, and `HERMES_TUI_SKILLS` is the
per-session skill-loading lever. `[file: ~/.hermes/planning/CLAUDE.md:76]`
`[file: ~/.hermes/planning/CLAUDE.md:80]` `[file:
~/.hermes/planning/CLAUDE.md:81]` `[file:
~/.hermes/planning/CLAUDE.md:128]` `[file:
~/.hermes/planning/viewer/server.py:1291]`

The worker reads ticket state through two channels: the viewer prompt wrapper
injects selected ticket/call context into every turn, and the worker can read
the active markdown files directly when grounding or editing. The wrapper
includes current planning date, tracker path, overview path, workspace path,
sprint paths, item section, title, Ticket ID, Chat ID, and selected context.
`[file: ~/.hermes/planning/viewer/server.py:1165]` `[file:
~/.hermes/planning/viewer/server.py:1183]` `[file:
~/.hermes/planning/viewer/server.py:1216]` `[file:
~/.hermes/planning/viewer/server.py:1226]`

The planner's code-level mutation helpers are markdown/API helpers, not a
separate worker CLI. `parse_workspace` reads `Ticket ID`, `Chat ID`,
`Readiness`, `Mode`, `Priority`, and `Sprint item`; it groups tickets into
rendered readiness sections from `Readiness:`. `[file:
~/.hermes/planning/viewer/server.py:417]` `[file:
~/.hermes/planning/viewer/server.py:527]` `[file:
~/.hermes/planning/viewer/server.py:590]` `[file:
~/.hermes/planning/viewer/server.py:618]`

`write_workspace_chat_id` writes the durable Hermes session key back into the
active `workspace.md` for either the selected item or the `Necessary Calls`
section. `[file: ~/.hermes/planning/viewer/server.py:968]` `[file:
~/.hermes/planning/viewer/server.py:974]` `[file:
~/.hermes/planning/viewer/server.py:982]`

`write_workspace_ticket_field` mutates only `mode`, `readiness`, or `priority`,
validates values, locates the ticket block by `Ticket ID` or legacy fallback,
and refuses to move a ticket to Ready unless Mode already exists. `[file:
~/.hermes/planning/viewer/server.py:999]` `[file:
~/.hermes/planning/viewer/server.py:1026]` `[file:
~/.hermes/planning/viewer/server.py:1043]`

The file write target is restricted to the active daily `workspace.md`; the
validator rejects non-workspace files and stale/non-active workspace paths.
`[file: ~/.hermes/planning/viewer/server.py:630]` `[file:
~/.hermes/planning/viewer/server.py:640]`

The visible viewer API exposes workspace chat and ticket-field endpoints:
`/api/workspace/chat-id`, `/api/workspace/ticket-field`, and
`/api/workspace/chats/turn`. `[file:
~/.hermes/planning/viewer/server.py:2370]` `[file:
~/.hermes/planning/viewer/server.py:2381]` `[file:
~/.hermes/planning/viewer/server.py:2392]`

The explored v1 planner files show no dedicated planning CLI equivalent to
`read ticket`, `propose`, or `advance` for the Workspace worker. In v1, reading
is prompt-wrapper plus markdown parsing, proposing is chat and/or direct
selected-surface markdown edit, and advancing readiness is changing the
`Readiness:` field through markdown/API mutation with the approval rules above.
`[file survey: ~/.hermes/planning/viewer/server.py, ~/.hermes/planning/CLAUDE.md,
~/.hermes/skills/productivity/workspace-ticket-shaping/SKILL.md]` `[file:
~/.hermes/planning/viewer/server.py:1192]` `[file:
~/.hermes/planning/viewer/server.py:1026]`

The generic Hermes Kanban worker uses structured tools, not the CLI, from inside
the agent. The worker-visible tools are `kanban_show`, `kanban_complete`,
`kanban_block`, `kanban_heartbeat`, `kanban_comment`, `kanban_create`, and
`kanban_link`; `kanban_list` and `kanban_unblock` are orchestrator-only.
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:7]`
`[runtime-injected file:
~/.hermes/hermes-agent/tools/kanban_tools.py:64]`
`[runtime-injected file:
~/.hermes/hermes-agent/tools/kanban_tools.py:1465]`

`kanban_show` is the read/orient operation for a Kanban task. It returns task
state, parents/children, comments, events, runs, and `worker_context`; the
spawn prompt is only `work kanban task <id>`, so body and handoffs arrive
through this tool. `[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:37]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:48]`

`kanban_complete` is terminal success with a human-readable summary,
machine-readable metadata, optional verified `created_cards`, and optional
deliverable `artifacts`. `[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:50]`
`[runtime-injected file:
~/.hermes/hermes-agent/tools/kanban_tools.py:1092]`

`kanban_block` is the human-help operation for genuine blockers; `kanban_comment`
is durable annotation; `kanban_heartbeat` is liveness/progress; `kanban_create`
spawns follow-up work; and `kanban_link` adds dependency edges. `[runtime-injected
file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:62]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:70]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:78]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:86]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/03-the-worker-protocol/kanban-tools.md:107]`

The Kanban dispatcher spawns a worker as `hermes -p <profile> --accept-hooks
chat -q "work kanban task <id>"` with environment variables for task id,
workspace, run id, claim lock, board/db/workspace root, profile, tenant, branch,
goal loop, and terminal cwd. `[runtime-injected file:
~/.hermes/hermes-agent/hermes_cli/kanban_db.py:7286]`
`[runtime-injected file:
~/.hermes/hermes-agent/hermes_cli/kanban_db.py:7312]`
`[runtime-injected file:
~/.hermes/hermes-agent/hermes_cli/kanban_db.py:7335]`
`[runtime-injected file:
~/.hermes/hermes-agent/hermes_cli/kanban_db.py:7349]`
`[runtime-injected file:
~/.hermes/hermes-agent/hermes_cli/kanban_db.py:7382]`
`[runtime-injected file:
~/.hermes/hermes-agent/hermes_cli/kanban_db.py:7393]`

Humans/operators have a `hermes kanban <verb>` CLI. It supports board
bookkeeping, create/swarm, list/show/context, comment/complete/block/schedule/
unblock/promote/archive, assign/reassign/reclaim/claim/heartbeat, link/unlink,
dispatch, watch/tail/log/runs, diagnostics, notifications, specify/decompose,
and gc. Agents in worker mode are explicitly told not to shell out to that CLI
for board operations and to use `kanban_*` tools instead. `[runtime-injected
file:
~/.hermes/hermes-agent/docs/kanban-guide/99-operator-surfaces/cli.md:3]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/99-operator-surfaces/cli.md:39]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/99-operator-surfaces/cli.md:62]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/99-operator-surfaces/cli.md:76]`
`[runtime-injected file:
~/.hermes/hermes-agent/docs/kanban-guide/99-operator-surfaces/cli.md:98]`
`[runtime-injected file:
~/.hermes/hermes-agent/agent/prompt_builder.py:260]`


## 5. Live agent self-description (agent-interrogation, 2026-07-08)

Drove `hermes chat -s workspace-ticket-shaping` against the real home and asked the worker to describe its own operating model. Verbatim below (the agent's own voice/jokes included; the substance is the source we distill from):

1. My job when I take on a ticket

I am not the executor by default. I am the Workspace ticket-shaping worker.

The system I sit inside is:

- `tracker.md` = the committed day plan / status surface.
- `overview.md` = judgement/orientation layer.
- `workspace.md` = the ticket-refinement surface.
- `Necessary Calls` = cross-cutting decisions outside the ticket tree.
- `Tickets` = stable ticket records with `Ticket ID:` and `Readiness:`.

The key quote is:

“Workspace is the non-canonical ticket-refinement and active-work surface: it takes work-tree tickets from fuzzy `Concepts` through `Needs Shaping` to `Ready`, and can represent active execution with `In Progress`.”

So when I “take on” a Workspace ticket, I am usually doing one of these:

- understanding what the ticket is really asking for;
- grounding it in today’s plan, sprint context, repo/docs/product context, or prior references;
- clarifying what success means;
- turning vague work into a better ticket;
- mapping options/tradeoffs;
- identifying what is still unresolved;
- helping move the ticket toward Ready;
- if already In Progress, helping continue/inspect/coordinate active work;
- if explicitly asked to execute a Ready ticket, executing within the ticket contract.

The selected ticket is implicit context. The viewer already injects the ticket fields into the chat. The skill says:

“The selected ticket is implicit context. The viewer's prompt wrapper already injects the ticket's fields… Trust the wrapper. Do not re-ask the user for context the wrapper already supplies.”

So I should not begin by parroting the ticket back or asking “which ticket?” like a confused filing cabinet with Wi-Fi.

I should ground myself before helping:

“Workspace detail chat should not engage from vibes when the ticket clearly belongs to a project, sprint item, repo, product surface, or code path.”

That means: if the ticket depends on code, docs, a product surface, sprint item, or existing plan, I inspect the relevant source narrowly before opining. Not a grand archaeological dig. Enough to avoid talking nonsense.

I must treat chat as chat, not a markdown review pane:

“Treat planner chat as chat, not a markdown review pane.”

So I use chat for:
- useful signals;
- decision framing;
- narrow answers;
- broad options;
- tradeoffs;
- discussion.

I do not paste giant proposed ticket rewrites unless asked.


2. Ticket stages

Concepts

Meaning:
A Concept is a fuzzy ticket. It may be only a thesis, concern, idea, or question. Success is not confirmed yet.

Concrete guidance:

“Concepts holds broad rough tickets where success is not confirmed yet.”

“If the apparent success is only the agent's best guess, keep the item in Concepts and name the unresolved success question instead of promoting it.”

What I do:
- preserve the user’s actual idea;
- keep it sparse if signal is sparse;
- ask or help discover what success would mean;
- lightly ground it if needed;
- avoid inventing success/approach/mode just to make it look tidy;
- identify whether it is even a ticket, a note, a call, or a broader campaign thread.

What I must not do:
- execute the underlying work;
- promote it because I can imagine a plausible outcome;
- add generic `Success`, `Approach`, or `Open questions` from vibes;
- add `Mode:` unless genuinely known and useful;
- convert a loose thought into implementation work;
- turn sparse user signal into a fake mini-spec.

Good outcome:
A good Concept preserves the real idea without lying about readiness. It may have only:
- title;
- `Body`;
- maybe a few genuine questions;
- maybe `Readiness: Concepts`.

Bad outcome:
A bloated Concept with invented “Success: improve X”, “Approach: clarify scope”, and “Mode: Autonomous” because the agent got bored and started doing admin cosplay.


Needs Shaping

Meaning:
The ticket has a short body and success is either user-confirmed or genuinely obvious, but the route is not settled.

Concrete guidance:

“Needs Shaping tickets have a short body and user-confirmed or genuinely obvious success criteria but still need the top-level ways of doing it explored and judged.”

“This is the top-level exploration stage: identify the main possible ways to do it, compare them, work out the parts/risks, and surface hard questions before picking an approach.”

What I do:
- clarify the route;
- map options;
- inspect repo/product/docs where needed;
- separate known facts from open decisions;
- strengthen `Success`;
- write or refine `Approach`;
- remove resolved open questions;
- make unresolved questions specific and useful;
- keep ticket text tight enough that the user will actually read it.

What I must not do:
- execute the work unless explicitly asked and scope is widened;
- smuggle execution into shaping;
- silently decide unresolved design/product questions;
- promote to Ready without explicit approval;
- add fake certainty;
- leave stale `Open questions` after the user has answered them;
- broaden into tracker/sprint/code/skill changes unless explicitly approved.

Good outcome:
A good Needs Shaping ticket says:
- what the work is;
- what success means;
- what route or routes are being considered;
- what still blocks executability;
- what needs user judgement vs agent investigation.

Bad outcome:
A ticket that technically has headings but no real clarity. “Approach: define the details worth defining” is not an approach. It is a sentence wearing a lanyard.


Ready

Meaning:
The ticket is executable. The intended executor can start now in the named mode without first asking what success means, which surface matters, or what judgement call is being made.

Concrete guidance:

“Ready holds tickets ready to execute in a named mode.”

“Ready tickets can be executed only when the user explicitly asks to execute them.”

“Ready cards must include `Mode: Autonomous | Supervised | Paired | Manual | Campaign`.”

Readiness test:

“Can the intended executor start now, in the named mode, without first asking what success means, which surface/path matters, or what judgement call is being made? If no, keep it in Needs Shaping…”

What I do:
- check that Body / Success / Approach / Mode are clear enough;
- make sure hidden product judgement is not being pretended away;
- include load-bearing paths, constraints, verification, or boundaries only when useful;
- if user explicitly asks to execute, treat ticket text as the contract;
- stay scoped to the ticket;
- verify real work if executing.

What I must not do:
- execute just because it is Ready;
- mark it Done because an agent claims it finished;
- use Ready as a synonym for “nice-looking ticket”;
- add “Suggested prompt” or routine “Scope/files” furniture unless actually useful;
- overbuild a Ready ticket into a mini-constitution.

Good outcome:
A Ready ticket is clear enough to launch. It has enough context and constraints for the mode:
- Autonomous: enough for an agent to work safely and return output.
- Supervised: enough to plan first and ask at checkpoints.
- Paired: enough to work slice-by-slice with the user.
- Manual: enough for the user to do it directly, with AI prep/support.
- Campaign: enough to act as a bundled disambiguation/execution surface elsewhere.

Bad outcome:
A Ready ticket that still asks “what are we trying to achieve?” or “where does this happen?” That is not Ready; it is wearing Ready’s hat.


In Progress

Meaning:
The ticket is actively being worked on. It is a readiness/work state, not a mode.

Concrete guidance:

“`In Progress` means the ticket is actively being worked on. It is not an execution `Mode:` and should not change or replace `Autonomous` / `Supervised` / `Paired` / `Manual` / `Campaign`.”

What I do:
- continue, inspect, resume, or coordinate active work;
- stay scoped to the ticket unless the user broadens it;
- check real repo/run/test state if asked how it is going;
- help handle blockers;
- report real progress, not vibes;
- if execution completes, leave the ticket visible until the user approves closeout.

What I must not do:
- treat In Progress as still just a shaping card;
- change mode to “In Progress”;
- close it because I or another agent says it is done;
- mark related sibling tickets done unless the user says so;
- let active work leak into unrelated files/surfaces.

Good outcome:
The ticket reflects real active work and can be resumed or reviewed. The user can see what is happening and approve closeout when satisfied.

Bad outcome:
An agent finishes something, removes the ticket, and only afterwards tries to remember what happened. Trace-loss with a tiny moustache.


Done

Done is not a Workspace readiness state. Done is a closeout workflow.

The Workspace tree is closed:

“Concepts → Needs Shaping → Ready → In Progress.”

When the user says “done”, “approved”, or equivalent, I treat that as completion approval for the selected ticket.

Concrete guidance:

“When the user types ‘done’, ‘this is done’, ‘approved’, or similar in the selected Workspace ticket chat, treat that as the completion signal for the selected ticket; no extra approval is needed.”

Completion order:
1. Write a concise outcome to today’s tracker `Done`.
2. Update any matching tracker parent/child status if needed.
3. Remove the ticket from active Workspace.
4. Parse/verify the result.

Concrete guidance:

“After the user approves/says done, first write the terse outcome to today's tracker `Done` section, then remove the ticket block from active `workspace.md`, then return/refresh planner data.”

What I do:
- record outcome, not implementation diary;
- split residual/follow-on work into a new ticket if needed;
- remove stale active-lane mentions;
- preserve sibling tickets unless explicitly also done.

What I must not do:
- create `Readiness: Done`;
- close based only on an agent report;
- remove before logging outcome;
- copy the entire implementation story into Done;
- resurrect completed work later unless evidence contradicts closeout.

Good outcome:
The tracker has a short useful Done outcome, Workspace no longer shows active completed work, and any remaining thread is represented as its own ticket.

Bad outcome:
A finished ticket still sitting in Workspace like haunted furniture, or a Done bullet that reads like a deployment log written by a printer having a breakdown.


3. Responsibilities and permission lines

What I may do on my own

In a selected ticket chat, if the user is shaping the selected surface and the edit is obvious, non-destructive, and scoped to that ticket, I may update it directly.

Concrete guidance:

“Routine selected-surface shaping edits are direct by default.”

This includes:
- tightening `Body`;
- refining `Success`;
- improving `Approach`;
- narrowing `Open questions`;
- adding small clarifying notes;
- adjusting `Mode` when useful and low-risk;
- resolving already-answered questions into durable ticket fields;
- removing duplication;
- grounding a ticket lightly in docs/repo and adding action-shaping facts.

I may also:
- inspect relevant files/docs/repo surfaces;
- use subagents for broad repo grounding;
- answer narrow questions first;
- recommend options;
- tell the user a ticket is not Ready;
- say “this belongs in Concepts” when success is unknown;
- underreport readiness rather than overreport it.

Important quote:

“It is much better to underreport readiness than overreport it.”

What requires the user

Requires explicit user approval:
- readiness changes, unless the user directly commands the move;
- execution of a Ready ticket;
- completion/Done closeout;
- removals of key information;
- major rewrites that change meaning;
- cross-file edits beyond agreed Necessary Calls follow-through;
- memory changes;
- skill changes;
- code changes;
- sensitive changes;
- anything the user asks to review first.

Concrete guidance:

“Ask first for: readiness changes, removals of key information, major rewrites that change the meaning of a ticket, cross-file edits beyond the call's agreed follow-through, memory/skill/code changes, sensitive changes, or anything the user explicitly asks to review first.”

Hard boundaries:
- If user says “don’t edit, just tell me”, I do not edit.
- If user says “ground truth first”, I inspect/report first and wait before mutating.
- If ticket is Concept/Needs Shaping, I do not execute the underlying work.
- If discussion is provisional, I keep it in chat until the idea is accepted.
- If user asks for exact changes, I give exact current/replacement blocks, not a polished strategy essay.

Concrete guidance:

“Do not edit when the user asked for an explanation.”

“Do not convert an investigation discussion into a decision before the user has actually chosen.”

“When the user asks for exact changes, be literal and match the requested altitude.”

Necessary Calls line

Necessary Calls are not tickets. They are decision prompts. Once resolved, I carry the decision into affected tickets/tracker/overview and remove the call if fully reflected.

Concrete guidance:

“Necessary Calls are decision prompts; once the call is made, update the relevant ticket(s), not the call itself.”


4. Good outcomes and good vs bad approaches

Good outcome in Concepts:
- preserves the idea;
- makes uncertainty visible;
- avoids fake fields;
- does not pretend success is known.

Bad:
- invented success;
- invented mode;
- generic open questions;
- implementation dressed as capture.

Good outcome in Needs Shaping:
- success is clear;
- main routes/options are visible;
- open questions are specific and readiness-reducing;
- approach is emerging but not fake;
- user judgement is separated from agent investigation.

Bad:
- bloated card;
- stale questions;
- silent product decisions;
- generic “clarify scope” fog;
- execution smuggled into shaping.

Good outcome in Ready:
- executor can start;
- mode is named;
- boundaries and verification are clear where needed;
- enough context exists without a code-tour;
- no hidden big judgement remains.

Bad:
- “Ready” but still needs foundational decisions;
- overlong spec;
- missing mode;
- vague success;
- paths/constraints invented rather than inspected.

Good outcome in In Progress:
- real active state is visible;
- work can be resumed;
- progress is backed by actual evidence;
- ticket remains until user approves completion.

Bad:
- agent reports “done” and ticket disappears;
- no verification;
- related work accidentally swallowed;
- stale tracker/workspace contradictions.

Good outcome in Done:
- short tracker outcome;
- active Workspace ticket removed;
- residuals split;
- parser/render state verified.

Bad:
- Done as a Workspace state;
- Done bullet as implementation diary;
- residual concern lost in chat sediment.

What makes a good approach

A good approach:
- starts with method/sequence, not background dump;
- reflects the actual work shape;
- names what happens first, then next;
- separates facts from choices;
- includes only constraints that change execution;
- is thin enough to read;
- leaves true execution questions for execution.

Concrete guidance:

“Write `Approach` as the method/sequence for doing the ticket, then details.”

“Ticket-writing baseline. Say the job plainly, say what done means, and include only constraints/context/known facts that change how the work should be done.”

“Keep tickets readable; move the essay elsewhere.”

What makes a bad approach

A bad approach:
- invents a route before grounding;
- dumps implementation guesses;
- uses labels instead of decisions;
- repeats the same detail across fields;
- turns philosophy into `Approach`;
- says “define/clarify/explore” without naming the actual missing choice;
- promotes the agent’s preferred option before the user has accepted it.

The skill’s best line on this:

“Bloat is bad; unverified bloat is worse, because it looks like thinking while quietly being administrative fog.”


5. Memory entries and skills I rely on

Hermes memory entries I actually rely on

These are the injected durable memories that materially affect this behaviour:

1. Planning system shape

“Planning: tracker/overview/workspace; Workspace=Calls+Concepts→NS→Ready; `Mode:`. Readiness: underreport; Concepts omit Success/no absence labels; NS=success known but path/surface unclear; Ready=executable. Sprint: 2wk LF/primary bet/supports; reset preserve triage.”

This is the compact operating model. It tells me:
- Workspace has Necessary Calls plus refinement tree;
- Concepts / Needs Shaping / Ready distinctions;
- `Mode:` is metadata;
- underreport readiness;
- Concepts should not fake missing Success.

2. Daily file primacy

“Planning: current daily file has primacy; read it first each session for planning help.”

This tells me the day-folder surfaces are the live context, especially outside narrow selected-ticket chats.

3. Ticket-shaping / capture style

“Default framing/capture/follow-up unless executing. Plans chat-only; tickets after settled. Source-ground first.”

This tells me:
- default is shaping, not execution;
- discussion stays chat-only until settled;
- source grounding comes before confident shaping.

4. Planning file edit style

“When editing markdown planning files, use small, exact, tightly anchored patches and reread the surrounding section first…”

This governs how I mutate workspace/tracker files when editing is allowed.

5. User’s ticket-writing preferences

“Planning: separate tasks=flat siblings; one user-framed item=one bullet+nested details; campaign parent=tracker/sprint; breakdown/disambiguation=ticket bullet. Child Concepts/scoping light…”

This gives the user’s preferred granularity:
- don’t split one user-framed item into fake siblings;
- don’t over-detail child Concepts;
- preserve rich archived ticket wording;
- no invented specifics.

6. User’s docs/spec shaping preferences

“Docs/spec shaping: lean/plain; use repo docs/system map/research first; organise by actual systems; focus on user flow + backend effects; spell out hard workflows; no dup/invent/extrapolate/theatre…”

This reinforces:
- plain language;
- source-grounding;
- no duplication;
- no invented theatre. A blessing, frankly.

7. Memory/skill boundary preference

“Memory edits need approval; changed entries only. Skill patches OK.”

This matters because Workspace shaping may reveal a workflow correction, but I cannot casually edit memory. Skills can be patched when routine/outdated, but memory needs approval.

8. Skill/workflow review preference

“Skill/workflow reviews: deliberate=file-by-file exact wording; routine skill patches need no approval.”

This matters when the user is asking for source behaviour or workflow reconstruction: be exact and quote concrete guidance.

Other skills I rely on

workspace-ticket-shaping

This is the active selected-ticket operating skill. It gives:
- selected ticket is implicit;
- direct routine edit ladder;
- non-execution rule for Concepts/Needs Shaping;
- readiness stages;
- permission boundaries;
- product-ticket shaping patterns;
- Done closeout workflow;
- plain-language / no-jargon guidance;
- all the Vylo-specific shaping references when relevant.

daily-planning

This gives the whole day-folder model:
- tracker / overview / workspace relationship;
- daily file primacy;
- 5am planning boundary;
- intraday capture;
- tracker Done rules;
- Workspace refinement tree from the wider planning system;
- readiness vocabulary;
- mode definitions;
- closeout style.

Most important quote from it:

“The tracker file is the committed plan. The overview file is the non-canonical judgement/orientation layer… The workspace file is the non-canonical ticket-refinement and active-work surface.”

planning-viewer

This gives the UI/system behaviour:
- selected ticket chat context;
- `Ticket ID:` vs `Chat ID:`;
- `Readiness:` controls rendering;
- `Mode:` is metadata;
- Workspace chat should not be a fake markdown review queue;
- viewer should inject ticket context;
- Workspace completion operation order.

Important quote:

“Every Workspace ticket under `## Tickets` should have a stable `Ticket ID:`… `Readiness:` is the ticket's refinement/work state… `Mode:` is attention-worth / focus metadata only.”

rollover

I use this only when day-boundary / end-of-day / new-day creation is the task. Workspace ticket shaping must not reimplement rollover. The daily-planning skill explicitly says rollover owns staged day transition.

sprint-planning

Used when the user asks for sprint review, sprint transition, sprint bet/support shaping, or sprint-level reconciliation. Workspace tickets can reference sprint items, but sprint transition is not this skill’s job.

planning-viewer, when the ticket is about viewer code

If the selected ticket is about the planner viewer itself — parser, rendering, chat plumbing, Workspace UI, `/workspace`, completion flow — I load/use `planning-viewer`, because it owns implementation surfaces and verification commands.

product-doc-co-shaping reference

Used when the ticket is really live concept/product-doc shaping. It tells me:
- capture only what the user said;
- one bucket at a time;
- defer research when asked;
- keep main docs slim;
- label agent suggestions as suggestions;
- correct over-extrapolation immediately.

Relevant quote:

“Capture only what the user actually said. Do not add sensible-sounding architecture, research questions, safety language, or product calls unless they were explicitly stated or clearly accepted.”

Domain references inside workspace-ticket-shaping

For Vylo and other recurring product-ticket families, I rely on the reference files listed in the skill, not memory alone. They give narrowly scoped shaping patterns for:
- real-life analysis;
- eval fixtures;
- interaction resolver;
- transcript contribution;
- UX/product-experience tickets;
- recurring user read;
- personas;
- waitlist;
- onboarding;
- UI rebuild;
- automatic conversation sources.

The operating rule is: if a ticket matches one of those domains, use the reference so I don’t reinvent the shape from vibes like a tiny consultant goblin.

The short version

My job is to move a visible Workspace ticket toward honest executability, not to make it look busy.

Concepts preserve uncertainty.
Needs Shaping resolves route/options.
Ready is executable with a mode.
In Progress is active work.
Done is user-approved closeout, not a readiness state.

I ground first, answer the narrow question first, edit directly only when the edit is obvious and scoped, ask for approval on readiness/execution/completion/sensitive/wider changes, and keep tickets lean enough that a human can actually use them.
