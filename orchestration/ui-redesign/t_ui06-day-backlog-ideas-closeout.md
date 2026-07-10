# t_ui06 — Day, Backlog, Ideas + closeout

Reference: `orchestration/daily-redesign/day.html`, `backlog.html`,
`ideas.html`; notes.md Backlog rev 2 / Ideas rev 2 (capture idiom unified on
the live ideas treatment; idea dates dropped — owner-directed).

## Outcome
The three remaining screens speak the voice; the capture idiom is unified;
dead CSS is swept; docs describe the shipped design.

## Contract
1. **Day** (`DayRoute.svelte` styles): dateline sans unchanged in structure;
   focus in `--font-serif` `--type-serif-hero`; three section bodies serif
   (take `--type-serif-lg`, watch/lands `--type-serif-md`); label colours
   (faint / amber / green) unchanged; still flat — no boxes, no seams. All
   `data-day-*` attributes preserved (30+ e2e uses — grep them all).
2. **Backlog capture re-projection** (`BacklogRoute.svelte`): the make
   disclosure keeps `data-create="item"` and the `+ New backlog item` summary;
   inside, the ideas-capture idiom replaces the labelled form: title input
   (serif `--type-serif-xl`, placeholder "What needs doing?"), description
   textarea (serif `--type-serif-sm/md`, placeholder
   "Why it matters, any context — optional"), then ONE foot row: project
   SegmentedControl, priority SegmentedControl, a small sans deadline input
   (placeholder "due YYYY-MM-DD — optional"), spacer, primary commit. Same
   fields, same payloads, same `data-input="title|deadline|body"` and
   `data-commit`; FormField usage here is removed (leave the FormField
   component if other call sites use it; if backlog was its only consumer,
   delete the component and its CSS and note it).
3. **Ideas** (`IdeasRoute.svelte`): capture restyled to the live idiom in the
   new voice — transparent unboxed inputs, italic placeholders (UI face),
   serif title (`--type-serif-xl`) and detail, hairline seam under the capture;
   foot row unchanged functionally (`↩` hint, Capture). **Drop the relative
   date from idea rows** (remove `relativeDayLabel` usage here and the `.when`
   span; if `relativeDayLabel` has no other consumer, delete it from
   `lib/dates.ts` and its unit tests — list this in the report). Rows keep
   `data-idea-id`, disclosure/flat split, project chips.
4. **Closeout**: sweep `assets/app.css` for rules orphaned by waves 01–06
   (grep each suspect class across web/src before deleting); update
   `docs/frontend.md` — the design language section (serif voice, amber-only
   accent, line diet, depth-on-the-ask) and any screen descriptions that
   changed shape (sprint one-scroll + documents page, backlog capture,
   review layout, user-note in the spine).

## e2e / selector notes
Grep for `data-day-`, `data-create`, `data-input`, `data-commit`,
`data-ideas`, `data-idea-id`, `.disclosure--make`, `.disclosure--idea`. The
ideas date-drop: grep e2e/unit for `when`/`relativeDayLabel` assertions and
translate/remove with the report listing each.

## Acceptance
Implementer slice green (+ `tests/e2e/test_backlog_ideas.py` and the day e2e
file(s)); design review against the three mockups; full `./verify` green at
integration; docs updated.
