# The conversation's three states — kickoff

Not dispatched. Owner design, 2026-07-26, from two mockups. Runs **after the record-carries-
content package**, so the composer it rearranges is already the finished one.

## What changes

The conversation stops being a panel alongside the ticket and becomes something at the
bottom of it, in three states.

**REST.** The composer, and above it a single line: while a turn runs, the working
indicator, the plan's progress and the newest tool call; at rest, the first line of
whatever happened last. Not "the agent's last message" — **whatever happened last, whoever
produced it**, so your own message can be the line and so can a waiting permission ask.

**PEEKED.** Clicking the input opens it a little. **The bar goes** — the turn head is
inside the transcript and keeping the bar would say the same thing twice, six pixels apart.
A card over the page.

**OPENED.** A control takes it full. It stops looking like a card, with a control back.

## Why the shape is right

Rest is not a new component. It is the turn head, the plan strip and the newest tool call
we already built — the one thing designed to summarise a whole turn without moving. When a
design falls out of the pieces that already exist, that is usually the design being right.

And the strongest argument for the whole placement is the thing the owner told me to ignore:
**tool approval stays in the input space**. That means at Rest, with the conversation
closed, a request that needs you is already on screen. You never open anything to find out
you are being waited on. A side panel cannot do that.

## Ruled

- **Nothing ever changes state on its own.** No auto-peek when a permission ask arrives, none
  when a turn starts. Only the person moves it.
  **Design around this**: the Rest bar therefore becomes the ONLY signal that something needs
  you. How it shows a waiting ask is its most important job, not a detail of it.
- **Peeked and Opened are a LAYER, NOT A MODE.** The ticket behind stays readable and
  scrollable, and clicking it drops the conversation back a state.
- **The state a conversation OPENS IN is a property the page sets**, from the start. Chief of
  staff may say opened, a paired page something else, and neither knows about the other.
  The owner: "we don't need to do all that yet, but we need to be able to." Building it in
  now is the difference between trying arrangements out and rebuilding it later.
- **Keep the state dumb.** One enum. Nothing remembered per ticket yet.
- **One conversation at three heights — NEVER three components or three mounts.** Otherwise
  every transition loses scroll position, loses the composer's draft, and restarts the
  optimistic message reconciliation.

## Corrected, so it is not re-derived

I claimed Peeked would break the anchored scrolling. The owner pushed back and was right.
The behaviour is written against the container's own height, so a short viewport simply
shortens the phase where nothing moves; and a message taller than the peek already exceeds
the usable height, so it follows immediately rather than stranding the reader on their own
text.

**The only real residue**: changing state changes height, so the reserved space and the
anchor must be re-measured on the transition rather than carried over from the previous
size.

## Deliberately not asked yet

Cheaper to answer once it can be seen than to decide in advance:

- whether Opened covers the nav or only the page beneath it
- what else dismisses it — Escape, or only the controls
- the per-page defaults, which the owner has already said are for trying rather than deciding

## Gate

Each state, and each transition between them, provable in the browser. Specifically that a
transition keeps the reader's position and the composer's draft — that is the failure this
design is most likely to produce and the one a person would notice immediately.
