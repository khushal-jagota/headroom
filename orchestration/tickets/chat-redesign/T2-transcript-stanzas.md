# T2 — Transcript: stanzas, seams, pills

Implements DESIGN.md §2–§5. Mockup: `orchestration/chat-redesign/mockup.html`
(Idle, Working, Compacting, Compacted, Failure scenarios).

## Files owned
- `web/src/components/acp/TranscriptView.svelte`
- `web/src/components/acp/ThoughtView.svelte` (becomes the stanza header or is
  replaced by a new stanza component — your call, name things for what they are)
- `web/src/components/acp/ToolCallCard.svelte` (becomes the step row/detail)
- `web/src/components/acp/PlanView.svelte` (delete — plan leaves the
  transcript; T3 renders it as the task pill)
- `web/src/lib/acp/conversationState.ts` — stanza grouping + expansion state
  (extend; do not break existing snapshot consumers you don't own)
- New component files under `web/src/components/acp/` as needed
- `assets/app.css` — transcript-related blocks only

## Behavior (see DESIGN §3–§5 for full detail)
- Stanza = thought + tool calls it drove, grouped in arrival order; new thought
  burst → new stanza. While activity is `thinking` there is ALWAYS exactly one
  live stanza, even with zero thought text; orphan tool calls attach to it.
- Collapsed one-liner: "Thought"/"Thinking" (12px mono; shimmer when live,
  reduced-motion safe) + italic serif first-line preview. Expanded: full
  thought text, then steps. Expansion persisted like today's thought/tool
  expansion. Flat — no left borders, no indentation.
- Steps: kind glyph (inline SVG per ACP kind), title, right status mark
  (✓ done green / ✕ failed red / spinner in_progress / ○ pending), `+n −n` on
  edit steps computed from diff old/new text. Step click → detail (DiffView or
  sunken mono output well). NO raw input/output disclosure — delete it.
- Failed step → its stanza and its detail arrive pre-expanded.
- Delivery markers: removed from the transcript entirely.
- Compaction: dashed centred divider, live shimmer / settled / failed variants.
- Turn-end pill: centred outline pill on activity `failed` (error, with
  `detail`) or `interrupted` (warn). Nothing on normal end.
- Protocol rejection + unsupported content: centred red-outline pill
  ("unsupported update" / short reason label) toggling a faint centred mono
  reason line. No bars, no insets.
- Messages (user bubble / agent prose / rich content) unchanged. Timestamps:
  none.

## Out of scope
Header (T1), permission (T4), task pill (T3), composer (T5). No new deps/tokens.

## Gates
`cd web && npm run check` (no new errors) and `npm run build` pass. Do NOT edit
test files (T6 owns them); list which tests broke in your report.
