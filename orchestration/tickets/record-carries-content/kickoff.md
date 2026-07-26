# The record carries content, not only text — kickoff

Not dispatched. Ruled in by the owner on 2026-07-26 to run **after the cutover and before
the conversation's three states**. Named for what it changes rather than for images,
because six separate gaps share one cause and naming it "images" would have hidden five of
them.

## The one cause

A conversation's send is a **string**, end to end: the composer takes text, `SendBody.text`
is a `str`, storage keeps a string, the transcript renders a string, and all three adapters
hand a string to their backend. The system it replaced carried **content blocks**.

Everything below follows from that single fact. Fix the cause and all six close; fix them
one at a time and you build the same path six times.

## What it closes

**1. Images.** The composer has no attach control. The one it replaced did a file picker,
drag and drop, paste, thumbnails, a remove control, a count badge and a drop overlay. The
browser half is close to free: `pendingConversationImages.js` and its test suite already
exist and were written against exactly this — it is a move, not a rewrite. The missing half
is the send path.

**2. Links in your own message.** The pane draws a prompt row as plain text, so a link you
paste stays literal. The agent's messages go through the markdown renderer, which replaces
every link and image with an inline preview. Your own words are the only thing in the
thread that does not get that.

**3. Resource links.** An agent handing back a file it produced, drawn as an embedded
preview, has no equivalent at all.

**4. Audio.** Same cause, no equivalent.

**5. Token usage and cost.** Reaches all three adapters, in three shapes that do not agree:
hermes discards usage TWICE — the per-turn counts where only the stop reason is read, and
the `usage_update` session update into a catch-all — and never sets cost at all. Codex is
not even subscribed: `thread/tokenUsage/updated` is dropped before parsing because the
notification was never added to the generated bindings, so this is the one piece that
touches machinery rather than code. Claude is the only backend with real money in it
(`total_cost_usd`, per-model `costUSD`) and it is discarded where only the terminal reason
is read. Above the adapters this needs a new sink member, somewhere in the record to put a
number, and probably a migration.

**6. The compaction marker.** The exact inverse: a compaction boundary exists NOWHERE BUT
the adapters. Codex is cheap — already parsed into a correctly typed item and dropped
through a default arm. Claude's is a system message with subtype `compact_boundary`, in the
catch-all. Hermes is dearest: the update type carrying it is not imported and the fact
lives in a `_meta` blob needing a reader. Above them nothing can carry it — no event kind,
no sink method, and both payload match statements are exhaustive and closed.

Compaction still HAPPENS today and always did; Panels never initiated it, the backends do.
What is missing is the record noting it — and that matters more than it sounds, because
without a marker a reader sees a transcript whose earlier context has silently gone with
nothing saying why.

## Also in scope

**The command menu**, adopting more of T3's shape (owner). Not the `/` button, which is
already carried across — the live, filterable menu built from what the agent reports it can
do. The new contract has no notion of available commands at all, so the backend must report
them. This is the only item that is not about content blocks; it is here because it is the
other half of the composer and doing the composer twice is waste.

## Sequencing inside the package

**The record change lands first, alone.** Items 1–4 all depend on it and would collide in
the same files. Once it has landed the three adapters are separate files and can go in
parallel — and usage, cost and compaction all touch those same three adapters, so they want
doing in that same wave rather than as a second pass over the same code.

## Two artefacts left standing

Both found during the cutover, both save an afternoon:

- The old pane's usage slot is still in the new pane. The `.chat-usage` class now shows the
  workspace folder.
- The compaction separator's stylesheet outlived the concept — the class survives in the
  new transcript, reused for the model-changed row.

## What is NOT here

The sub-agent counter. It touches the same three adapters and would therefore be cheap to
build in this wave, and the owner has ruled it out anyway: nice to have, not necessary,
last or another time. Efficiency does not outrank priority. Its research is in the working
notes if it is ever wanted.

## Gate

Each of the six must be provable by a test that fails when its change is reverted. The
cutover found three browser specs that had been quietly wrong for days, so passing is not
the question — the question is whether it fails on the old behaviour.
