# docs/ conventions

Guidance for Claude when creating or editing files under `docs/`. The root
`CLAUDE.md` always loads; this file loads only when working in this directory.

`docs/` is the plain-language account of what the planner is and how it works —
for a smart reader who does not write code. A section a non-engineer can't follow
without reading the source is a section to rewrite, not to leave.

## The bar

Write or keep a doc only when the _why_ or the shape of a system isn't already
visible elsewhere. "Elsewhere" is the source code (including docstrings),
`PROGRESS.md` (the live build snapshot), `decisions.md` (judgment calls),
`DESIGN.md` (the visual language), and git history. When a fact is derivable from
code, let the code carry it. When a fact is explained in one place, do not restate
it in a second — duplication rots, and two of the copies will drift wrong.

## Keep it current, not frozen

These docs describe the system **as it is now**. When a feature changes or is
removed, the doc that described it is wrong and must be corrected in the same
breath — a doc still describing deleted machinery is a bug, not history. (History
is git's job; the live build state is `PROGRESS.md`'s.) A doc is not a build log
and never narrates stages ("Stage 4 did X"); it states what stands today.

## What a system doc carries

- **Leads with what the system _is_ and what it's _for_** — where its work lands
  in the product — then the load-bearing decisions. A doc is an editorial cut, not
  a catalogue: drop anything that doesn't earn its place.
- **One ASCII diagram of the shape** near the top — ordered stages where order is
  the point, or a two-region split for a non-linear system. Never a call-graph of
  function names. Mermaid and other renderer-dependent formats don't display here.
- **Plain language, the system's own words** — never the code's vocabulary as
  substance (signatures, types, control flow). Names appear only in the code-path
  pointers.
- **Code paths** under each section — where the system lives, folder-level mostly,
  a file where it pinpoints. Enough to trace doc → code, not exhaustive. This is
  the only place code names belong.
- **Honest, not aspirational.** State what actually works today, including when
  that's thin or blocked. A thin system written as thin is the point; a blocker
  named is worth more than a blocker hidden.
- **Handoffs** — where this system hands to another: one line on the neighbour
  plus a pointer to its doc.
- **Deferred** — one section at the end for known gaps and obligations, each bound
  to a _trigger_ ("when recovery ships," "before real users"), never a stage number.
- A footer: `_Last verified: <date>._` — when the doc was last checked against code.

## The map

`README.md` is the entry point — the list of systems and how they connect. Read it
first to find which doc owns a question. It names the systems; this convention
file does not (that rots).

## Cross-cutting rules

- **Duplication rots.** Pick the one location that best fits the reader and delete
  the rest.
- **Triggers, not stages.** Deferred items bind to conditions, never to stage numbers.
- **Diagrams must be ASCII.**
- **Length is not a goal.** Shorter is better, but never at the cost of a
  load-bearing claim. Cut what doesn't survive the bar; keep what does.

## Delete a doc when

- Its system was removed (correct or delete — never leave it describing a ghost).
- Its content moved wholesale into code, a docstring, or a sharper doc.
- It duplicates a fact another doc now owns better.
