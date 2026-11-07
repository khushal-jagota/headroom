# Design

Headroom's interface is meant to feel like a quiet desktop: near-black surfaces, almost no chrome, and content that is the thing itself. The owner built the tool, so the screen does not explain it back to him. [`DESIGN.md`](../DESIGN.md) holds the principles and `assets/tokens.css` the values. This page is how they show up in the product and how screens are assembled.

## How the principles show up

- **One accent, one meaning.** The soft steel accent means a person is needed: a pending approval, a required action, the enabled Send control. It stays out of ordinary agent prose. The approval control is the one strongly raised surface, so the gate is the most visible thing on a screen. Settled state is quiet. Done and error have their own small marks and are not the accent.
- **Content only.** A conversation turn is its text and a Ticket field is its value. Labels, avatars, timestamps, and counts appear only when they do work.
- **Asymmetry.** The owner's message is a contained pill. The agent's message is bare prose. See [Conversations](conversations.md).
- **Depth without borders.** Wells are recessed, scroll areas fade into their bars, and menus share one shadow. A hairline appears only where a list has a real seam. Selection is a sunken surface, not a border.
- **Feedback for anything that waits.** A running agent shows streaming dots or a working mark, and loading shows a skeleton shaped like what is coming. A surface is never silent while work is in flight.
- **Shared scales.** Type, space, and radius come from one set of tokens, with a single sans face. Monospace remains for code, diffs, and tool output.

## The page kit

Screens are assembled from a small shared kit rather than a new layout per feature:

- a screen header with a title and at most one piece of meta;
- rows for lists, with space as the separator;
- disclosures for detail that does not deserve a page;
- skeleton regions that load in the shape of their content;
- few buttons: a rare primary action, a quiet action, and a pill;
- chips and pills that name a Project, a count, or a state;
- a stage mark that carries Ticket and conversation state.

Some screens add layout rules in `assets/app.css`, keyed by a `data-screen` name. Most screens rely on the kit alone.

Goal pages use a separate page kit for checklists, charts, tables, layouts, and text. It is owned by [Goals](goals.md).

## The conversation is the strictest screen

Focus mode hides tool noise and Full shows the notebook. The toggle is a lens on one transcript, not two views. The composer is recessed and shows the model and effort actually in force. Other screens borrow its rules, which makes it the best place to see a violation.

## Decisions that shape it

- Token edits retune the whole app, and kit edits retune every screen that uses them. A new screen that adds a second accent, a heavy border on every card, or a caption on an obvious control has left the system.

## Code

`assets/tokens.css`, `assets/app.css`, and `web/src/components/` for the kit, including `ScreenHeader`, `ListRow`, `StageMark`, `Button`, `SkeletonRegion`, `Disclosure`, and `conversation/`. The goal page kit is in `web/src/components/pageKit/`.

_Last verified: 2026-11-04._
