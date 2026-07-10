# UI redesign implementation — program plan

Implements the approved visual redesign across all screens. The **reference** is
`orchestration/daily-redesign/`: the approved mockups (`review.html`,
`workspace.html`, `ticket.html`, `day.html`, `sprint.html`, `sprint-docs.html`,
`backlog.html`, `ideas.html`) plus `notes.md`, which records every owner ruling
(what was approved, corrected, rejected, and the three approved additions).
The identity: **the system speaks in serif (Newsreader); the machine's controls
stay sans.** Amber remains the only accent (= needs you), spinners mean working,
green settled, red errored. Depth is rationed to the one ask surface per page.

## Ground rules (bind every ticket)

1. **Fidelity is judged against the mockups** on: structure, serif/sans
   placement, colour semantics, the line diet (which hairlines exist), component
   anatomy, and copy. Pixel values normalize onto the token scale (table below);
   px-perfection is not the bar, faithful translation is.
2. **The e2e selector contract.** All `data-*` attributes are preserved exactly
   (name and value) unless the ticket explicitly lists a change, in which case it
   lists every affected e2e assertion and its equivalent translation. Every class
   an implementer touches gets grepped in `tests/e2e/` and `tests/unit/`;
   selector translations keep assertions equivalent, never weaker.
3. **No behavior change** beyond the enumerated approved deltas:
   shell presence count, review keyboard shortcuts, review layout (skip/open
   top-right, no queue position), leash-as-sentence, user-note into the ticket
   spine (collapsed), sprint one-scroll + documents page + project grouping +
   derived day/fraction readouts, backlog capture re-projection, ideas capture
   restyle + date drop, serif voice everywhere content speaks.
4. **Verification split.** Implementers run `cd web && npm run build && npm run
   check && npm test` plus ONLY their screen's e2e file(s) directly
   (`.venv/bin/python -m pytest tests/e2e/<file> -x`). Full `./verify` is run by
   the orchestrator only, strictly serialized, once per wave.
5. **Implementers never commit.** The orchestrator commits one green wave at a
   time on this branch.
6. **CSS consolidates with markup.** A wave that restyles a screen deletes the
   styles it replaces in the same wave. No parallel old/new styling left behind.
7. **Fonts are self-hosted.** Newsreader via `@fontsource` packages imported in
   `web/src/main.ts` — no CDN, no Google Fonts link. The sans stack stays
   `--font-ui` (system). If a needed optical size/weight isn't available via
   fontsource, vendor the woff2 files under `web/src/assets/fonts/` instead.
8. **One implementer, serial waves.** All tickets share `assets/app.css` and
   routes — no parallelism. Lessons from each wave's reviews feed the next
   dispatch.

## Type normalization table (mockup px → tokens)

New tokens in `tokens.css` (the serif ladder; sans ladder unchanged):

    --font-serif: "Newsreader", Georgia, serif;
    --type-serif-sm: 15px;    /* chat bodies, note bodies, idea bodies (mockups 14.5–15.5) */
    --type-serif-md: 16.5px;  /* content prose: ticket fields, proposals, recaps, day sections, sprint field values (16–17) */
    --type-serif-lg: 18px;    /* leads: sprint bet, day brief-take (17.5–18) */
    --type-serif-xl: 21px;    /* capture title inputs (21) */
    --type-serif-display: 28px; /* screen/entity titles (27–28) */
    --type-serif-hero: 30px;  /* day focus (30) */

Mockup sans sizes (11/12/12.5/13/13.5/14) normalize onto the existing
`--type-xs`/`--type-sm` pair at the implementer's judgment; the design review
checks the result still reads like the mockup.

## The per-wave pipeline

For each ticket, in order:
1. Orchestrator dispatches the ticket to the persistent Opus implementer
   (context accumulates across waves; prior review lessons included).
2. Implementer implements + runs its verification slice (rule 4) + reports.
3. **Code review (Codex,** read-only, effort high; **xhigh** for t_ui02/t_ui03
   — the approval surfaces): concrete violations against the ticket contract,
   the e2e selector rule, and Svelte/CSS correctness.
4. **Design review (separate reviewer):** given the reference mockup file(s),
   the relevant notes.md section, the implementation diff, and — once the
   screenshot harness works — side-by-side PNGs of the mockup and the built
   app seeded with demo data. Verdict: per-decision fidelity checklist
   (voice placement, line diet, anatomy, colours, copy), each point pass/fail.
5. Orchestrator fixes or routes fixes, then runs full `./verify` (serialized),
   then commits the wave.

Screenshot harness (orchestrator-owned, scratch): a script that boots
`panels serve` on a free port with a temp data dir, seeds demo content through
the HTTP API (a project, a sprint with items/tickets in mixed states, a pending
plan proposal, chat messages, ideas, backlog items), screenshots each surface
with Playwright, and screenshots the corresponding mockup file. Built during
wave 1; if seeding a given state proves disproportionate (e.g. a live streaming
turn), the design review for that state falls back to diff-vs-mockup reading
plus the orchestrator's own eyeball, stated in the review record.

## Waves

- **t_ui01 — Foundations + shell.** Newsreader self-hosted; serif tokens; shell
  nav restyle per mockups; presence count ("N working" + small spinner) in the
  shell from the already-polled queues resource (`running_agents`).
- **t_ui02 — Ticket document.** Serif voice across title/recap/values/
  proposals/notes/chat; header facts line + status display + leash sentence;
  user note into the spine (collapsed, no mark, first); line diet; ask-block
  depth on ApprovalBlock; chat bodies serif.
- **t_ui03 — Review chamber.** Approved layout (skip/open top-right with
  chevrons, labelled recap + notes disclosure, ask depth + entrance rhythm,
  send-back row, re-set empty state with real copy); keyboard shortcuts.
- **t_ui04 — Workspace floor.** Rail restyle (chief row, one-line filters,
  project sections, row emphasis for needs-you rows); chief pane serif voice.
- **t_ui05 — Sprint restructure.** One scroll; documents to their own route;
  collapsible project groups incl. loose-tickets-as-group; item rows with
  status word + done-fraction; ticket rows priority · title · state; derived
  day-of-sprint and fractions.
- **t_ui06 — Day, Backlog, Ideas + closeout.** Day serif re-voice; backlog
  capture re-projection; ideas capture restyle + date drop; dead-CSS sweep;
  docs/frontend.md updated to describe the shipped design.

## Success criteria

Every wave: its named e2e/unit tests green through full `./verify`, Codex code
review with no unresolved violations, design review with every checklist point
passing or explicitly waived by the orchestrator with a reason, one commit.
Program done when all six waves are merged on the branch, `./verify` is green,
and docs describe the shipped state.
