# Chat panel redesign — design intent

Owner-approved redesign of the ACP conversation panel. The interactive mockup
(`mockup.html`, open in a browser) is the visual source of truth; this file states
the behaviors and the wire data that drives each piece. `catalogue.html` documents
what the panel rendered before this redesign.

Ground rules: keep the existing token system (`assets/tokens.css`) — spacing on
the existing 4px grid, existing colors/type/radii; no new tokens without need.
Keep the reactivity model (events → keyed invalidation → targeted refetch) and
the existing `ConversationSnapshot` projection approach. No new dependencies.

## 1. Header (silent by default)

One row: left, the worker's display name only — no ticket number, no backend
name, no dot, no activity word. Right, usage as `41.2k / 200k · $0.34`
(compact k-formatting, tabular numerals; cost only when present) from
`usage_update` (`used`, `size`, `cost{amount,currency}`); omit the whole right
side if the backend never emits usage. Also right of usage: a quiet `⋯`
overflow button opening a small menu holding **New conversation** (with a
confirm step) — owner ruled it must not sit below the composer; header is its
home. The old Stop/New-conversation button row below the composer is removed.

Header exceptions — the only times the header speaks, mid-row in mono:
- `waiting for you` (accent text) — pending permission
- `compacting` — compaction in flight
Connection trouble is a single red 7px dot appearing left of the worker name
(no words). Healthy/working/idle/failed: header stays silent. Failure is
signalled in the thread only (§5).

The one-line `ConversationStatus` strip (label-priority funnel) is retired; its
screen-reader alert behavior (`role="alert"` on new errors) is preserved
somewhere equivalent and invisible.

## 2. Transcript: messages

Unchanged voice: user = right-aligned serif bubble (the only bubble); agent =
full-width bubble-less serif prose (markdown). No timestamps, no avatars.
Rich content blocks (image/audio/resource_link/resource) render as before.
Delivery markers (`accepted`/`queued`/`started`…) leave the transcript
entirely — their information lives in the queue tray (§7) and receipt line.

## 3. Transcript: stanzas (thinking + nested tool calls)

A **stanza** is one beat of work: a Thinking line plus the tool calls that
thought drove, in arrival order. Grouping: `agent_thought_chunk`s open/extend
the current stanza's thought; subsequent `tool_call`/`tool_call_update`s attach
to the current stanza; the next thought burst starts a new stanza.

- **Always a Thinking line while a turn runs** — even if the runtime never
  emits thought text (activity `thinking` with no chunks → bare live stanza).
  Tool calls arriving with no preceding thought attach to it.
- Live: the word "Thinking" shimmers. Settled: the word "Thought".
- Collapsed (default): one line — word (12px mono) + first line of thought text
  as an italic serif preview, ellipsized. Nothing else. No duration, no counts.
- Expanded (click): full thought text (serif, muted) then the steps.
  Expansion persists per stanza (same persistence approach as today's
  thought/tool expansion).
- **Flat**: no left borders, no indentation anywhere — nesting is expressed by
  disclosure only.

Steps (one per `toolCallId`):
- Kind glyph from ACP `kind` (13px stroke icons: read/edit/delete/move/search/
  execute/think/fetch/switch_mode/other; unknown → other).
- Title from `title` (mono path fragments styled as today's mock).
- Right-aligned status mark: `✓` green (completed), `✕` red (failed), spinner
  (in_progress), `○` faint (pending).
- Edit steps append `+n −n` counts computed client-side from diff content
  (`oldText`/`newText` line diff).
- Clicking an expandable step reveals its detail: diff (DiffView) or
  command/terminal output (sunken mono well). Raw input/output is **dropped**
  (owner ruling) — no raw JSON disclosure.
- A stanza containing a failed step arrives **pre-expanded**, with the failed
  step's detail open. Collapse-by-default must never hide a failure.

## 4. Transcript: seams and pills (all centred, flat)

- **Compaction**: dashed full-width divider, centred uppercase mono label.
  Live: `context compacting · {trigger}` with shimmer. Settled:
  `context compacted · {trigger}`. Failed: `context compaction failed ·
  {reason}` (same divider, error color text). Driven by `context_compaction`.
  No token counts (not on the wire).
- **Turn end**: when a turn ends any way other than normally, a centred
  outline pill: `turn failed · {detail}` (error) / `interrupted` (warn), from
  activity `failed`/`interrupted` + its `detail` string. Normal end: nothing.
- **Protocol rejection** (`protocol_update_rejected`): centred red-outline
  pill `unsupported update`; clicking toggles the reason in faint centred mono
  below. **Unsupported content items get the same pill treatment.**

## 5. Failure presentation

Header silent; nothing under the composer. The centred `turn failed` pill plus
the pre-expanded failing stanza are the entire signal.

## 6. Task pill (plan)

An in-flow strip (fixed height ~34px, space reserved in every state) between
thread and composer. While a turn is active and a plan exists: centred pill
`{done} / {total} tasks` (tabular numerals) with a spinner beside it only when
activity is `thinking`/`compacting` (not while `waiting_for_permission`).
Hover/focus reveals a popover above listing entries: `✓` done (struck,
faint), `›` in progress (strong), `○` pending. Driven by `plan` updates
(full-replace; totals are honest); cleared by `plan_removed`. Plan no longer
renders inline in the transcript.

## 7. Composer

- **Queue tray**: queued prompts (`queue_snapshot`) render as a tray fused to
  the top of the sending box — same sunken surface, inset horizontally
  (margin ≈ space-2), top corners rounded, bottom edge open; **the sending box
  keeps its full native rounding in all states**. Rows: mono number, one-line
  summary, `×` cancel (per-item cancel action). Hairline between rows.
- **Delivery choice**: while a turn is active, a small segmented pill control
  in the composer footer (left of send): `queue` (default) · `send now` ·
  `steer`; steer disabled (not hidden) when `supportsSteer` is false. Hidden
  when idle (sends as `normal`).
- **Send ⇄ Stop**: one control. Idle: `↑` (accent-filled when armed). Active:
  a red-outline `■` Stop in the same spot → cancel action.
- **Receipt line**: only failures speak — a mono error line for rejected
  deliveries / composer errors. Nothing on success. Nothing below the box in
  the turn-failed state.
- Placeholder: `Message {worker}...` (worker display name, no ticket number).
- Slash menu, image attach/previews/drop target: unchanged from today.

## 8. Explicitly dropped (owner rulings)

Raw tool input/output disclosure; permission "submitting" state visuals (keep
buttons disabled during submit, no status text); backend name in header; token
counts on the compaction seam; timestamps; delivery markers in transcript;
ticket number anywhere; per-turn stop-reason wording beyond what activity
`detail` already carries.

## 9. Permission prompt

Between thread/task-strip and composer, as today, first pending request only.
Borderless accent surface (`--accent-surface`, radius-md, no border). Top row:
uppercase mono kind chip (tool call `kind`) + title + right-aligned mono
`m:ss` countdown to `deadlineAt`. Diff content renders in a sunken well.
Options row lays out the server's options (`{optionId, name, kind}`) by kind:
reject\* left as ghost buttons (red only on hover/intent); allow\* gather
right; the last allow option is the filled-accent primary. Labels are the
server's `name` verbatim. Selecting sends `permission_response`; while
awaiting the outcome, buttons disable (opacity only — no status text).

## 10. Wire-data notes for implementers

Everything above is drivable from the existing browser stream: envelopes
(`acp_session_update` with its 13 update kinds, `activity`, `connection`,
`queue_snapshot`, `context_compaction`, `permission_request`/`outcome`,
`delivery_receipt`, `human_echo`, `terminal_state`,
`protocol_update_rejected`) — see `src/planner/conversation/wire_contracts.py`
and `web/src/lib/acp/contracts.ts`. No server changes are in scope.
