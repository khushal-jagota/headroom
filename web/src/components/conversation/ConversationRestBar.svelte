<script lang="ts">
  /** The line above the composer at rest.
   *
   * One line, and at rest it is the whole of the conversation that is on the screen. The
   * job it exists for is the loud one: nothing in this design opens itself, so a
   * permission ask that is waiting shows here or the person never learns about it at all.
   * That case is the brightest thing the line can be — the accent the app uses for "needs
   * the human", and the white mark it uses for "only you can answer this".
   *
   * The rest of the time it is as quiet as the turn head it is made of: the same mono
   * line, counting the same seconds off the same instant, so moving the conversation
   * between its states never changes what the number says.
   */
  import {
    elapsedSecondsSince,
    formatDuration,
    millisecondsUntilNextSecond
  } from "../../lib/conversation/transcript";
  import type { RestLine } from "../../lib/conversation/restLine";
  import TaskProgress from "./TaskProgress.svelte";

  let { line }: { line: RestLine | null } = $props();

  /** The one thing on this line that changes on its own. Nothing else is touched by a
   *  tick, and the line is redrawn from the record as the record changes. */
  let elapsedSeconds = $state<number | null>(null);
  // Held apart from the line itself so a record that changed under a running turn does
  // not restart the counting.
  let workingSince = $derived(line?.workingSinceUnixMilliseconds ?? null);

  $effect(() => {
    const begun = workingSince;
    if (begun === null) {
      elapsedSeconds = null;
      return;
    }
    let waiting: ReturnType<typeof setTimeout> | undefined;
    // Each tick is aimed at the next whole second since the turn began, and the number is
    // worked out from that instant every time — so nothing drifts and nothing accumulates.
    const tick = (): void => {
      const now = Date.now();
      elapsedSeconds = elapsedSecondsSince(begun, now);
      waiting = setTimeout(tick, millisecondsUntilNextSecond(begun, now));
    };
    tick();
    return () => clearTimeout(waiting);
  });
</script>

<div class="c2-rest" class:is-waiting={line?.waiting ?? false} data-conversation-rest-bar>
  {#if line}
    <!-- Four independent axes, four shapes, never words. A turn can be running while a
         reply nobody has read is still above it, so these are drawn together. -->
    <span class="c2-marks" data-conversation-rest-marks>
      {#if line.marks.running}
        <span
          class="c2-mark c2-mark--running"
          data-conversation-mark="running"
          role="img"
          aria-label="working"
        ></span>
      {/if}
      {#if line.marks.unreadReply}
        <span
          class="c2-mark c2-mark--reply"
          data-conversation-mark="reply"
          role="img"
          aria-label="a reply you have not read"
        ></span>
      {/if}
      {#if line.marks.needsYou}
        <span
          class="c2-rest-dot c2-mark"
          data-conversation-mark="needs-you"
          data-conversation-rest-waiting
          role="img"
          aria-label="needs you"
        ></span>
      {/if}
      {#if line.marks.failed}
        <span
          class="c2-mark c2-mark--failed"
          data-conversation-mark="failed"
          role="img"
          aria-label="the turn failed"
        ></span>
      {/if}
    </span>
    {#if line.taskProgress}
      <TaskProgress progress={line.taskProgress} variant="rest" />
    {:else if workingSince !== null}
      <span class="c2-rest-working">{formatDuration(elapsedSeconds ?? 0)}</span>
      <span class="c2-rest-seam" aria-hidden="true">·</span>
    {/if}
    {#if line.who}
      <span class="c2-rest-who" data-conversation-rest-who>{line.who}</span>
    {/if}
    <span class="c2-rest-line" data-conversation-rest-line>{line.text}</span>
    {#if line.taskProgress && elapsedSeconds !== null}
      <span class="c2-rest-aside" data-conversation-rest-aside>{formatDuration(elapsedSeconds)}</span>
    {:else if line.aside}
      <span class="c2-rest-aside" data-conversation-rest-aside>{line.aside}</span>
    {/if}
  {/if}
</div>

<style>
  /* The same mono line the turn head is, because at rest it is standing in for it. */
  /* The top of the composer's own card, not a bar above it. It carries the card's sides
     and its rounded top, sits on the page's colour rather than the recessed one the input
     uses, and rests flush on the box below — whose own top edge is the line between them,
     which is why that edge is squared off and no rule is drawn here. */
  .c2-rest {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    min-width: 0;
    max-width: 100%;
    /* Inside the card, above the line. It draws nothing of its own: the card carries the
       surface and the outline, and the line beneath is the well's own top edge. */
    padding: var(--space-3) 0;
    /* Every step on this line is two brighter than it would be on the card. The text scale
       is set against the near-black base, and this surface is lighter than that, so the
       bottom of the scale reads at 3.3 to one here — under the floor the scale exists to
       keep. Muted is the first step that clears it, and the line sits a step above that
       again because a status nobody reads is not doing its job. */
    color: var(--text-default);
    font-family: var(--font-mono);
    /* The conversation's own scale: the line a reader is expected to read at rest is a
       step up from the app's smallest label. */
    font-size: var(--type-sm);
    line-height: 1.5;
    min-block-size: calc(1.5em + var(--space-3) + var(--space-3));
    letter-spacing: var(--tracking-mono);
  }
  /* The marks sit together at the head of the line and keep their own order, so a
     conversation that gains one does not move the words beside it. */
  .c2-marks {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }
  .c2-marks:empty { display: none; }
  .c2-mark { flex: none; display: block; }
  /* Working: the turn head's own spinner, at the size of the line. */
  .c2-mark--running {
    width: 11px;
    height: 11px;
    border-radius: var(--radius-pill);
    border: 1.5px solid rgba(230, 210, 175, 0.18);
    border-top-color: var(--text-muted);
    animation: c2-mark-spin 900ms linear infinite;
  }
  @keyframes c2-mark-spin { to { transform: rotate(360deg); } }
  /* A reply nobody has read: the accent the app already means "there is something here"
     by, pointed at the conversation. */
  .c2-mark--reply {
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 5px 0 5px 7px;
    border-color: transparent transparent transparent var(--accent-bright);
  }
  /* A turn that failed, in the app's own error colour and its own shape. */
  .c2-mark--failed {
    width: 9px;
    height: 9px;
    border-radius: var(--radius-sm);
    background: var(--accent-error);
    transform: rotate(45deg);
  }
  @media (prefers-reduced-motion: reduce) {
    .c2-mark--running { animation-duration: 0.01ms; }
  }
  /* The brightest mark in the palette, and the app already means one thing by it: an ask
     only this person can answer. It is the same mark a Worker's row carries. */
  .c2-rest-dot {
    flex: none;
    width: var(--space-2);
    height: var(--space-2);
    border-radius: var(--radius-pill);
    background: var(--accent-needs-me);
  }
  .c2-rest-working {
    flex: none;
    font-variant-numeric: tabular-nums;
  }
  .c2-rest-seam { flex: none; color: var(--text-faintest); }
  .c2-rest-who { flex: none; color: var(--text-default); }
  .c2-rest-who::after { content: "·"; padding-inline-start: var(--space-1); }
  /* One line whatever is in it: what will not fit is cut here rather than wrapping the
     bar into two rows and moving the composer down the page. */
  .c2-rest-line {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-strong);
  }
  .c2-rest-aside {
    flex: none;
    color: var(--text-default);
    font-variant-numeric: tabular-nums;
  }
  /* Being waited on is not a state to read past: what is being asked comes up to the
     brightest text in the scale, beside the accent that says it needs answering. */
  .c2-rest.is-waiting .c2-rest-line { color: var(--text-strong); }
</style>
