# Design

Headroom’s interface is meant to feel like a quiet desktop: near-black surfaces, almost no chrome, and content that is the thing itself. A conversation turn is its text. A Ticket field is its value. Labels, avatars, timestamps, and counts appear only when they do work. The owner built the tool. The screen does not explain the tool back to him.

The standing principle list is [`DESIGN.md`](../DESIGN.md). The values those principles tune live only in `assets/tokens.css`. This page is how those choices show up in the product.

## One accent, one meaning

The accent is the soft steel `--accent-bright`. It means a person is needed: a pending approval, a required action, the selected row. It is not a brand decoration, and it stays out of ordinary agent prose. If it is everywhere, it says nothing. The one strongly raised surface is the approval control, with a top highlight and a long shadow. Nothing else on a page is elevated that way. A pure white dot is reserved for a Worker waiting on a permission ask, so “only you can answer this” reads before any label does.

Settled state is quiet. An errored stage can use the error color. Done can use the small green mark. Those are status, not the accent.

## How a page is built

Screens share a small kit rather than a new layout per feature.

- A screen header carries the title and, when needed, one piece of meta.
- Lists are rows. A seam appears where a real list needs one: the stage spine, a Manager’s children, Ideas. Elsewhere, separation is space.
- A disclosure opens detail that does not deserve a page.
- Loading is a skeleton in the shape of the content that is coming, document-shaped or row-shaped, and it gives way when the body arrives.
- Buttons are few: a primary action, a quiet action, a pill. The primary action is rare.
- Chips and pills name a project, a count, or a state. They are not decoration.

Each screen hangs its layout off a `data-screen` name in `assets/app.css`, using the shared type, space, and radius scales. New sizes are a design decision. The scale already includes the larger prose, display, and hero sizes the owner asked to keep when serif was removed. Content and controls use one system sans. Monospace remains for code, diffs, and tool output, because alignment carries meaning there.

Depth comes from a recessed well, not from borders. The composer, editors, and code sit in that well. Menus and floating notices use one shared shadow. A scrim dims whatever is behind an open layer. Hover is a faint light laid over the element’s own surface, so it does not assume what color is underneath.

## The conversation is the strictest screen

The person’s message is a right-aligned pill. The agent’s message is bare prose, with compact closed thought and tool rows. One status line says whether the turn is working. The composer is recessed and carries the model and effort actually in force, plus Steer, Queue, Send now, Stop, and New when they apply. Focus hides the tool noise. Full shows the notebook. The toggle is a lens on one transcript, not two products.

Fade replaces dividers. A scrolling thread dissolves into the bars above and below it. A gateway round trip is slow enough to feel. The screen shows dots that hand off to streaming, and a working mark on a running Ticket, so a surface is never frozen and silent while an agent is in flight.

## What feeds the look, and where it shows up

Token edits retune the whole app. Component edits in the kit retune every screen that uses them. The Ticket stage spine, Review’s approval card, the Workspace rail’s selected row, and the conversation pane are the places the rules are easiest to see and easiest to break. A new screen that introduces a second accent, a border around every card, or an explanation of an obvious control has left the system.

Copy uses employee, ceiling, and holder. The Worker is an employee taking the task. The ceiling is the grant. The holder is who a parked proposal is addressed to.

## Goal pages and managed files

Goal pages use the shared page kit for checklists, charts, tables, layouts, and text. They are dashboards for the user. Explicit display choices control what the page shows. See [Goals](goals.md).

Manager and goal artifacts use sibling roots. Manager files retain `files/sprint-items/<manager_id>/` and `/files/managers/` links. Goal files use `files/goals/<goal_id>/` and `/files/goals/` links. Their routes support reads, text writes, and deletion through the existing owner, Chief, or own-agent authority. Both use safe paths, symlink checks, media types, and `nosniff`.

Goal workspaces use the shared Artifacts catalog and preview frame. Completion, deactivation, and conversation reset preserve the files. Managed file previews retain authored context and artifact links.

## Pointers

`assets/tokens.css`, `assets/app.css`, [`DESIGN.md`](../DESIGN.md), and `web/src/components/` for the kit: `ScreenHeader`, `ListRow`, `StageMark`, `Button`, `SkeletonRegion`, and `web/src/components/conversation/`.

_Last verified: 2026-10-30._
