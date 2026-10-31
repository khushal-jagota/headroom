# DESIGN.md — the planner's visual language

The *values* live in `assets/tokens.css` (the only place a colour, radius, spacing step,
duration, or type size is written — never hardcode one). This file is the *how*: the
principles those tokens serve. Warm near-black surfaces, one accent — the soft steel
`--accent-bright` — that means "needs the human", a shared sans type scale. Restraint
over decoration. How those choices show up in the product is `docs/design.md`.

Distilled from `tokens.css`, `PRINCIPLES.md`, and the Vylo conversation reference (no-divider fades,
bubble-less assistant, recessed composer).

## Surface roles

- `--surface-1` is the main neutral plane; `--surface-2` is the contained neutral
  plane for cards, menus, controls, and the human message bubble.
- `--surface-recessed` is for wells, editors, code, and persistent selections.
- `--surface-ink` is the cool near-black conversation band and human-input panel;
  it also supplies dark foreground ink on bright fills.
- `--interaction-hover` is a translucent image over the element's own surface.
  It highlights the local foreground without assuming what lies beneath it.
- `--scrim` dims the background behind an open layer. It is not the inverse of
  hover and does not share hover's strength.
- `--shadow-float` is the common shadow for menus, popovers, and floating notices.

## Principles

1. **Omit, then omit again.** Every element is guilty until proven load-bearing. Cut labels,
   chrome, and "helpful" text. The owner built this — don't explain it back to him ("remembers
   the thread", session ids, hint bars: gone).

2. **Content only.** Strip per-item decoration. No avatars, name labels, timestamps, counts, or
   action buttons unless they do real work. A conversation turn *is* its text; a ticket field *is* its
   value.

3. **Fade, don't divide.** Prefer no lines. Let scroll areas dissolve into solid bars (a mask
   gradient), and get depth from a recessed surface (`--surface-recessed` + an inset shadow) or a
   raise, not borders. Spend a `--border-color` hairline only where a real seam is needed.

4. **One accent, one meaning.** `--accent-bright` means *needs the human* — a pending
   approval, a required action. Never decorative. If the accent is everywhere it says nothing; that
   is why it stays out of the conversation thread and shows up on the gate.

5. **Asymmetry carries meaning.** Different things look different. The human's message is a
   contained pill; the agent's is bare prose. The gate is the accent; settled state is quiet. Don't
   make everything a rounded card with a shadow.

6. **Scannable and self-evident.** Design billboards, not brochures — visual weight equals
   importance. Use conventions (chevrons, a search glyph, top-left identity) over cleverness. If
   it needs an instruction to use, redesign it, don't caption it.

7. **Feedback for anything that waits.** A real gateway round trip is ~10s. Show it: dots that
   hand off to smooth streaming, a working marker on a running ticket. Never leave a surface
   frozen and silent.

8. **Use the shared scales.** Typography uses 11, 13, 15, 18, 20, 28, and 30px.
   The owner asked to preserve sizes while removing serif, so the 18px prose,
   28px display, and 30px hero sizes are an explicit exception to the five-size
   limit. Existing sans size exceptions remain unchanged. The radius and spacing
   scales stay fixed; new sizes need an explicit design decision.

9. **One sans voice.** Content and controls use the same system sans stack
   (`--font-ui`). Hierarchy comes from size, weight, spacing, and emphasis. Former
   serif text keeps its larger 18px prose, 28px display, and 30px hero sizes.
   Smaller body text uses 15px and section titles use 20px. Code, diffs, tool output,
   and `code`, `pre`, `kbd`, and `samp` retain monospace because alignment carries meaning. No italics except
   input placeholders.

10. **Depth is rationed to the ask.** The one raised approval surface earns a top highlight
    and a long soft shadow, with the accent Approve glowing beneath.
    Nothing else on any page is elevated. Hairlines survive only where a real list needs a seam
    (the ticket stage spine, manager and idea rows) and at the conversation rail edge; blocks are
    otherwise separated by space, not lines.

## Applied so far

- **The conversation** (`web/src/components/conversation/`, `web/src/lib/conversation/`,
  conversation styles in `assets/app.css`) — one right-aligned user pill (the only bubble),
  bubble-less **worker** prose, compact closed thought and tool details, one persistent
  status line, explicit permissions, and a recessed composer carrying the model and effort
  in force plus Steer, Send Now, run-when-free, Stop and New when applicable.
- **Shared typography across every screen** (`assets/tokens.css` type scale;
  `web/src/routes/*` + the `[data-screen="…"]` blocks in `assets/app.css`) — one
  system sans voice for content and controls, with monospace for aligned technical
  text. Size, spacing, and emphasis carry the hierarchy.

## Framing

The per-ticket agent is an **employee** taking on the task (not a "mind"). Its **ceiling** — the last
thing it may do on its own before it proposes and waits — is the grant, and its **holder** is who that
proposal is addressed to. Use employee / ceiling / holder language in copy.
