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
    millisecondsUntilNextSecond,
    workingSentence
  } from "../../lib/conversation/transcript";
  import type { RestLine } from "../../lib/conversation/restLine";

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

{#if line}
  <div class="c2-rest" class:is-waiting={line.waiting} data-conversation-rest-bar>
    {#if line.waiting}
      <span class="c2-rest-mark" data-conversation-rest-waiting>
        <span class="c2-rest-dot" aria-hidden="true"></span>
        <span class="chat-state chat-state--attn">waiting for you</span>
      </span>
    {:else if workingSince !== null}
      <span class="c2-rest-working">{workingSentence(elapsedSeconds)}</span>
    {/if}
    {#if line.who}
      <span class="c2-rest-who" data-conversation-rest-who>{line.who}</span>
    {/if}
    <span class="c2-rest-line" data-conversation-rest-line>{line.text}</span>
    {#if line.aside}
      <span class="c2-rest-aside" data-conversation-rest-aside>{line.aside}</span>
    {/if}
  </div>
{/if}

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
    /* Inside the card, above the well. It carries the accent surface rather than the
       card's own, because at rest this line is the whole of the conversation on screen and
       it should read as a thing rather than as text lying on the card. The inline padding
       comes with the surface: text flush against the edge of a colour reads as a mistake. */
    padding: var(--space-3);
    border-radius: var(--radius-md);
    background: var(--accent-surface-bright);
    /* Every step on this line is two brighter than it would be on the card. The text scale
       is set against the near-black base, and this surface is lighter than that, so the
       bottom of the scale reads at 3.3 to one here — under the floor the scale exists to
       keep. Muted is the first step that clears it, and the line sits a step above that
       again because a status nobody reads is not doing its job. */
    color: var(--text-default);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
  }
  .c2-rest-mark {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
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
