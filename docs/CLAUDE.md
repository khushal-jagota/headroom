# docs/ conventions

Guidance for agents editing `docs/`. The root `AGENTS.md` always loads. This file loads when the work is in this directory.

`docs/` is the top-level manual for Headroom. The readers are Khushal and coding agents. `docs/README.md` is the map. It names the pages. This file does not.

## The shape of a focused page

Explain what the thing is in the product, how we think about building and running it, what feeds it, what it feeds, and where a person or an operator meets it. Mechanism and product story come first. Light code detail comes after. A module map of files, ownership, and connections is optional garnish at the end.

Order:

1. What it is, and why it exists.
2. How it works.
3. What feeds it, and what it feeds.
4. Where it shows up in the product or in operations.
5. Light code pointers.
6. Optional short module map or invariants, last.

Prefer a separate focused page over a combined dump. A thin system stays thin. A page that only says which module connects to which module does not earn a place.

## Keep one source, and keep it current

These pages describe the system as it is now. When a feature changes or is removed, correct the page in the same change. A page that still describes deleted machinery is a bug. History belongs to git. Current judgment belongs on the Ticket that owns the work. A page is not a build log and does not narrate how a change was staged.

Write a page when the why or the shape is not already visible in the page that owns the topic. When a fact is explained in one place, point to it. Do not restate it. Duplication rots, and the copies drift.

`DESIGN.md` remains the visual principle list. `docs/design.md` is how those principles show up. `CONTEXT.md` remains the word list. `PRINCIPLES.md` remains the engineering rules. Do not copy them into a second essay.

## Craft

- Plain language and the product’s own words. Code names, routes, and commands appear where an operator or an agent must use them, and again as pointers at the end. They are not a substitute for the explanation.
- An ASCII diagram when shape is the point: a flow, a ladder, who stands above whom. Do not force one into a page that is clearer without it. Do not use a call graph of function names. Mermaid does not display here.
- Honest about what works today, including when that is thin or blocked.
- Name the neighbor and link to its page when two systems meet.
- A known gap binds to a condition, never to a stage number. Omit the section when the page has no known gap.
- End with `_Last verified: <date>._` when the page was checked against the code.

## Delete a page when

- Its system was removed.
- Its content now lives in a sharper page, and the old one would compete.
- It duplicates a fact another page owns better.

_Last verified: 2026-10-30._
