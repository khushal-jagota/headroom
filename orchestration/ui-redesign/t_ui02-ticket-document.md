# t_ui02 — Ticket document

Reference: `orchestration/daily-redesign/ticket.html`; notes.md sections
"Owner feedback on pass 3", "Fidelity audit", per-surface Ticket notes.

## Outcome
The ticket screen reads as the mockup: serif voice for everything said, sans
for controls; three-line header; recap open; user note inside the spine;
per-stage Notes; line diet; the ask block with depth; chat in serif.

## Contract
1. **Header** (`TicketRoute.svelte`): title in `--font-serif`
   `--type-serif-display`; facts line = status display (dot + label, amber only
   when the status means "needs you": awaiting_approval; running keeps the
   spinner idiom via existing markers) followed by the existing EnumPill/Pill
   controls (priority, due, project/sprint) and Take over / Copy as quiet text
   actions; **the leash sentence** replaces the two labelled scope pills:
   "approved until [ceiling picker] then [at_cap picker]" — the SAME EnumPill
   components and options, recomposed inline with connecting words (keyLabels
   move into the sentence). `data-scope-ceiling`/`data-scope-atcap` wrappers and
   `data-deadline`, `data-copy`, `data-ticket-takeover-toggle`,
   `data-ticket-status`, `data-marker` all preserved exactly.
2. **Recap** stays above, open, serif body (`--type-serif-md`);
   `data-recap`/`data-content-section="recap"` preserved.
3. **User note moves into the spine**: first entry of the stage group, the same
   Disclosure primitive as stages but with no stage mark (blank spacer),
   **collapsed by default**, label "User note". `data-user-note` and
   `data-content-section="user-note"` stay on it (grep: 11 e2e uses of
   `data-user-note` — verify each still passes; the container moving is fine,
   the attributes and the InlineEdit inside must survive).
4. **Stage spine**: `TicketStageSection` visuals per mockup — sans uppercase
   stage names, ✓/●/○ marks unchanged in meaning, serif field values
   (`--type-serif-md`), **every stage keeps its Notes disclosure** (collapsed
   unless written). `data-field`, `data-stage-state`, `data-content-section`
   values unchanged.
5. **The ask** (`ApprovalBlock`): raised surface gains the depth treatment
   (top-highlight inset + long soft shadow + faint glow under Approve, from the
   mockup's `.ask`/`.approve` rules); content serif; scope picker copy is the
   live "until … then …" (ScopePairPicker — confirm current words match the
   mockup; if they already do, no change). All `data-approval-block`,
   `data-mode`, `data-accept`/`data-approve`, `data-edit` preserved.
6. **Line diet**: blocks separate by space; hairlines remain ONLY on stage-spine
   rows and the chat rail seam. Delete the replaced block-border rules.
7. **Chat rail** (`ChatPanel` styles): message bodies serif `--type-serif-sm`;
   header (dot + mono label), thread fade mask, composer box, buttons — already
   live; do not restructure, only the body typography changes. All chat
   `data-*` untouched.
8. Old ticket-screen styles that this replaces are deleted in this wave.

## e2e / selector notes
Grep `tests/e2e` and `tests/unit` for: `data-user-note`, `data-recap`,
`data-field`, `data-content-section`, `data-approval-block`, `data-scope-`,
`.disclosure-body`, `.disclosure-summary`. The user-note relocation must keep
`[data-user-note]` queries working (it may now be inside the spine container).
**Known concrete translations (from the spec review — handle each):**
- `tests/e2e/test_flows_a.py` (~line 847 region) reads/focuses/edits the
  InlineEdit under `[data-user-note]` directly — the note is now collapsed by
  default, so the test must open its disclosure summary first (equivalent
  assertions, plus one assertion that it IS collapsed by default).
- `tests/e2e/test_ticket_file_previews.py` (~line 283) waits for a preview
  inside `[data-user-note]` — same: open the disclosure first.
List both translations (and any further hits the grep finds) in the report.

## Acceptance
Implementer slice green (web build/check/test + `tests/e2e/test_flows_a.py` or
the file(s) covering ticket surfaces — identify by grep); design review checklist
against ticket.html passes; full `./verify` green at integration.
