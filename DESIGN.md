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

8. **The scales are closed.** Five type sizes, the 4/8/12 radius steps, the fixed spacing scale.
   Snap to them; don't invent a size for a one-off. A new category needs evidence of need
   (`PRINCIPLES.md`), not taste.

## Applied so far

- **Ticket chat** (`web/src/components/ChatPanel.svelte`,
  `web/src/components/ChatComposer.svelte`, chat styles in `assets/app.css`) — one
  right-aligned user pill (the only bubble), bubble-less **employee** prose, no dividers, a recessed
  composer with a `/` trigger for the gateway command catalog, thinking dots.

## Framing

The per-ticket agent is an **employee** taking on the task (not a "mind"). Its **scope** — how far it
can go without your approval — is the grant/ceiling. Use employee / scope language in copy.
