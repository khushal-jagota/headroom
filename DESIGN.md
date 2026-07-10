# DESIGN.md — the planner's visual language

The *values* live in `assets/tokens.css` (the only place a colour, radius, spacing step,
duration, or type size is written — never hardcode one). This file is the *how*: the
principles those tokens serve. Warm near-black surfaces, one amber accent that means
"needs the human", a closed five-size type scale. Restraint over decoration.

Distilled from `tokens.css`, `PRINCIPLES.md`, and the Vylo chat reference (no-divider fades,
bubble-less assistant, recessed composer).

## Principles

1. **Omit, then omit again.** Every element is guilty until proven load-bearing. Cut labels,
   chrome, and "helpful" text. The owner built this — don't explain it back to him ("remembers
   the thread", session ids, hint bars: gone).

2. **Content only.** Strip per-item decoration. No avatars, name labels, timestamps, counts, or
   action buttons unless they do real work. A chat turn *is* its text; a ticket field *is* its
   value.

3. **Fade, don't divide.** Prefer no lines. Let scroll areas dissolve into solid bars (a mask
   gradient), and get depth from a recessed surface (`--surface-sunken` + an inset shadow) or a
   raise, not borders. Spend a `--border-color` hairline only where a real seam is needed.

4. **One accent, one meaning.** `--accent-bright` (amber) means *needs the human* — a pending
   approval, a required action. Never decorative. If amber is everywhere it says nothing; that
   is why it stays out of the chat thread and shows up on the gate.

5. **Asymmetry carries meaning.** Different things look different. The human's message is a
   contained pill; the agent's is bare prose. The gate is amber; settled state is quiet. Don't
   make everything a rounded card with a shadow.

6. **Scannable and self-evident.** Design billboards, not brochures — visual weight equals
   importance. Use conventions (chevrons, a search glyph, top-left identity) over cleverness. If
   it needs an instruction to use, redesign it, don't caption it.

7. **Feedback for anything that waits.** A real gateway round trip is ~10s. Show it: dots that
   hand off to smooth streaming, a working marker on a running ticket. Never leave a surface
   frozen and silent.

8. **The scales are closed.** The sans type sizes, the 4/8/12 radius steps, the fixed spacing
   scale. Snap to them; don't invent a size for a one-off. A new category needs evidence of need
   (`PRINCIPLES.md`), not taste. The one added category is the **serif ladder** (`--font-serif`
   plus its six `--type-serif-*` sizes) — the voice below earns it.

9. **The system speaks in serif; the machine stays sans.** Everything the product or its agents
   *say* is set in serif (Newsreader) — ticket titles and prose, recaps, proposals, notes, field
   values, day and sprint bodies, chat messages, the capture inputs, the empty-state lines.
   Everything that is a *control or a fact* stays in the sans UI face — the nav, labels, pills,
   buttons, filters, counts, key hints, status words, dates. The split *is* the hierarchy: on any
   surface the words are the content and the small sans elements are the machinery. No italics
   except input placeholders.

10. **Depth is rationed to the ask.** The one raised approval surface earns a top-light gradient,
    a top highlight, and a long soft shadow, with the solid-amber Approve glowing beneath.
    Nothing else on any page is elevated. Hairlines survive only where a real list needs a seam
    (the ticket stage spine, the sprint/ideas/backlog rows) and at the chat rail edge; blocks are
    otherwise separated by space, not lines.

## Applied so far

- **Ticket chat** (`web/src/components/ChatPanel.svelte`,
  `web/src/components/ChatComposer.svelte`, chat styles in `assets/app.css`) — one
  right-aligned user pill (the only bubble), bubble-less **employee** prose, no dividers, a recessed
  composer with a `/` trigger for the gateway command catalog, thinking dots.
- **The serif redesign, across every screen** (`assets/tokens.css` serif ladder;
  `web/src/routes/*` + the `[data-screen="…"]` blocks in `assets/app.css`) — the serif/sans voice
  split, the amber-only accent, the line diet, and the single depth-bearing ask surface, applied
  to Day, Review, Workspace, Ticket, Sprint, Backlog, and Ideas, plus the shell presence readout.

## Framing

The per-ticket agent is an **employee** taking on the task (not a "mind"). Its **scope** — how far it
can go without your approval — is the grant/ceiling. Use employee / scope language in copy.
