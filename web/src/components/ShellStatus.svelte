<script lang="ts">
  import { onDestroy, tick, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import type { ConnectionStatus } from "../lib/changeStream";
  import { deploymentStatusExpiryDelay, resolveDeploymentStatus, resolveStatusCard, type StatusCardKey } from "../lib/deploymentStatus";
  import { queries } from "../lib/queryCatalogue";
  import { releaseState, requestReleaseReload } from "../lib/releaseMonitor";
  import UpdateActions from "./UpdateActions.svelte";
  import { activeUpdateRequest, usableUpdate } from "../lib/updates/logic/actions";
  import { RAIL_MOTION } from "../lib/workspaceRailMotion";

  // The deploy this card narrates is what produces the release the monitor finds, so
  // both ends of that one event share this slot. One card occupies it at a time.
  type Card = { word: string; state: "changing" | "settled" | "wrong"; action: string | null; sentence?: true };
  const CARDS: Record<StatusCardKey, Card> = {
    "owner-update": { word: "Software update", state: "settled", action: null },
    Deploying: { word: "Deploying", state: "changing", action: null },
    Deployed: { word: "Deployed", state: "settled", action: null },
    Reconnecting: { word: "Reconnecting", state: "changing", action: null },
    Problem: { word: "Problem", state: "wrong", action: null },
    "update-ready": { word: "New version ready", state: "settled", action: "Refresh page" },
    // A held update states its own reason, so it carries no control. The sentence
    // wraps, because a truncated one cannot be read.
    "update-held": { word: "Page refresh waits until this message is safe", state: "changing", action: null, sentence: true }
  };

  let { connectionState }: { connectionState: ConnectionStatus } = $props();
  let clock = $state(Date.now());
  const updates = createQuery(() => queries.updates());
  const deployment = createQuery(() => queries.deploymentStatus());
  const statusState = $derived(resolveDeploymentStatus(deployment.data, connectionState, clock));
  const baseStatusWord = $derived(resolveStatusCard(statusState, $releaseState));
  const hasOwnerUpdate = $derived(activeUpdateRequest(updates.data?.request) ||
    (updates.data?.configured && usableUpdate(updates.data.available, clock)) || updates.data?.request?.state === "failed");
  const statusWord = $derived(baseStatusWord ?? (hasOwnerUpdate ? "owner-update" : null));
  let displayedWord = $state<StatusCardKey | null>(null);
  const card = $derived(displayedWord === null ? null : CARDS[displayedWord]);
  let open = $state(false);
  let closing = $state(false);
  let slot = $state<HTMLDivElement | null>(null);
  let resize: Animation | null = null;

  // The slot's height has one lifecycle and one rule: every handover starts from the
  // size on screen. The 0fr to 1fr row carries an entrance and an exit on its own, but
  // it cannot carry a swap between two open occupants, and it cannot be handed a
  // different starting size part way through one. So three moments take the height
  // here — a swap, a collapse, and a return during that collapse — and each reads the
  // rendered size first. Opposite directions share the rule, so neither can expose a
  // size the user was not already looking at.
  //
  // A cancelled animation settles its promise after the replacement is already in
  // hand, so cleanup releases the handle only while it still owns it. Otherwise a
  // later handover has nothing to cancel, and a stale effect outlives it.
  function animateHeight(from: number, to: number, delay = 0): void {
    dropResize();
    if (!slot || typeof slot.animate !== "function") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (Math.abs(to - from) <= 0.5) return;
    const animation = slot.animate([{ height: `${from}px` }, { height: `${to}px` }],
      { duration: RAIL_MOTION.space, delay, easing: RAIL_MOTION.ease, fill: "backwards" });
    resize = animation;
    void animation.finished.catch(() => {}).then(() => {
      if (resize === animation) resize = null;
    });
  }

  // The endpoint is always the card's own height. The slot is not a reliable endpoint:
  // its row can still be entering or reopening, so measuring it targets an intermediate
  // size, and the height snaps to the full card when the effect ends. The card does not
  // move with the row, because the row clips it rather than sizing it.
  function cardHeight(): number | null {
    const card = slot?.querySelector<HTMLElement>(".status-card");
    if (!card) return null;
    return card.getBoundingClientRect().height
      + (parseFloat(window.getComputedStyle(card).marginBottom) || 0);
  }

  // One contract for a handoff that keeps the card: leave from the size on screen, land
  // on the card's own height. A swap and a return during the collapse both use it.
  function takeHeight(previous: number): void {
    const target = cardHeight();
    if (target === null) { dropResize(); return; }
    animateHeight(previous, target);
  }

  // The collapse leaves from the size on screen. Cancelling the swap first would put
  // the slot back at the taller intrinsic height for the row transition to start from,
  // which reads as a jump up before the fall. The row still collapses underneath on the
  // same schedule, so both land on zero together.
  function collapseHeight(): void {
    if (!resize || !slot) { dropResize(); return; }
    animateHeight(slot.getBoundingClientRect().height, 0, RAIL_MOTION.exit);
  }

  function dropResize(): void {
    resize?.cancel();
    resize = null;
  }

  // The slot leaves with the component. A detached node keeps a running effect alive.
  onDestroy(dropResize);
  const motionStyle = `--glide-space:${RAIL_MOTION.space}ms;--glide-fade:${RAIL_MOTION.fadeIn}ms;--glide-delay:${RAIL_MOTION.fadeDelay}ms;--glide-exit:${RAIL_MOTION.exit}ms;--glide-ease:${RAIL_MOTION.ease};--glide-enter:${RAIL_MOTION.enter};--glide-leave:${RAIL_MOTION.leave}`;

  $effect(() => {
    const now = Date.now();
    clock = now;
    const delay = deploymentStatusExpiryDelay(deployment.data, now, connectionState);
    if (delay === null) return;
    const timer = window.setTimeout(() => { clock = Date.now(); }, delay);
    return () => window.clearTimeout(timer);
  });

  $effect(() => {
    const now = Date.now();
    clock = now;
    const expiry = Date.parse(updates.data?.available?.expires_at ?? "");
    if (!Number.isFinite(expiry) || expiry <= now) return;
    const timer = window.setTimeout(() => { clock = Date.now(); }, expiry - now + 1);
    return () => window.clearTimeout(timer);
  });

  $effect(() => {
    const word = statusWord;
    let obsolete = false;
    let frame: number | undefined;
    let timer: number | undefined;
    if (word !== null) {
      // A swap between two open occupants owns its own distance. An entrance does not:
      // the row glide already carries it.
      const replacing = untrack(() => open && displayedWord !== null && displayedWord !== word);
      const previousHeight = replacing && slot ? slot.getBoundingClientRect().height : null;
      // A return during the collapse keeps the size the collapse had reached.
      const returningHeight = untrack(() =>
        !replacing && !open && resize !== null && slot ? slot.getBoundingClientRect().height : null);
      displayedWord = word;
      closing = false;
      // Establish collapsed layout before entrance. Updates retain the card,
      // including a reversal during exit.
      void tick().then(() => {
        if (obsolete) return;
        if (previousHeight !== null) { takeHeight(previousHeight); return; }
        if (slot) void slot.offsetHeight;
        frame = window.requestAnimationFrame(() => {
          if (obsolete) return;
          open = true;
          if (returningHeight === null) { dropResize(); return; }
          // Take the height only once the row is open, so the restored card is measurable.
          void tick().then(() => { if (!obsolete) takeHeight(returningHeight); });
        });
      });
    } else {
      collapseHeight();
      open = false;
      closing = true;
      timer = window.setTimeout(() => {
        displayedWord = null;
        closing = false;
      }, RAIL_MOTION.exit + RAIL_MOTION.space);
    }
    return () => {
      obsolete = true;
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  });
</script>

{#snippet cardBody(entry: Card)}
  <span class="status-mark" aria-hidden="true"></span>
  <span class={entry.sentence ? "status-sentence" : "status-word"}>{entry.word}</span>
  {#if entry.action}<span class="status-action">{entry.action}</span>{/if}
  {#if deployment.data?.detail && (statusState === "problem" || statusState === "preparing" || statusState === "restarting" || deployment.data.state === "unknown")}
    <span class="status-detail">{deployment.data.detail}</span>
  {/if}
{/snippet}

<div bind:this={slot} class="status-slot" data-shell-status
  data-open={open ? "" : undefined} data-closing={closing ? "" : undefined}
  aria-hidden={statusWord === null ? "true" : undefined} style={motionStyle}>
  <div role="status" aria-live="polite" inert={closing ? true : undefined}>
    {#if card}
      {#if card.action}
        <button type="button" class="status-card" data-state={card.state}
          data-connection-status data-connection-state={connectionState} data-status-state={statusState}
          onclick={requestReleaseReload}>
          {@render cardBody(card)}
        </button>
      {:else}
        <div class="status-card" data-state={card.state}
          class:with-update-actions={displayedWord === "owner-update" || displayedWord === "Problem"}
          data-connection-status data-connection-state={connectionState} data-status-state={statusState}>
          {#if displayedWord !== "owner-update"}
            {@render cardBody(card)}
          {/if}
          {#if (displayedWord === "owner-update" || displayedWord === "Problem") && updates.data}
            <UpdateActions status={updates.data} now={clock} />
          {/if}
        </div>
      {/if}
    {/if}
  </div>
</div>


<style>
  .status-slot { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--glide-space) var(--glide-ease); }
  .status-slot > div { min-height: 0; overflow: hidden; }
  .status-slot[data-open] { grid-template-rows: 1fr; }
  .status-slot[data-closing] { grid-template-rows: 0fr; transition-delay: var(--glide-exit); }
  .status-card {
    display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2); margin-bottom: var(--space-2);
    border-radius: var(--radius-sm); background: var(--accent-surface); color: var(--accent-text); font-size: var(--type-xs);
    opacity: 0; transform: translateY(var(--space-1));
    transition: opacity var(--glide-fade) var(--glide-enter) var(--glide-delay), transform var(--glide-fade) var(--glide-enter) var(--glide-delay);
  }
  .status-slot[data-open] .status-card { opacity: 1; transform: none; }
  .status-slot[data-closing] .status-card {
    opacity: 0; transform: translateY(calc(var(--space-1) * -1));
    transition: opacity var(--glide-exit) var(--glide-leave), transform var(--glide-exit) var(--glide-leave);
  }
  .status-card[data-state="wrong"] { background: var(--error-surface); color: var(--text-strong); }
  .status-detail { flex-basis: 100%; }
  .status-card:has(.status-detail), .status-card.with-update-actions { flex-wrap: wrap; }
  .status-mark { width: 7px; height: 7px; flex: none; border-radius: var(--radius-pill); background: var(--accent-bright); }
  .status-card[data-state="wrong"] .status-mark { background: var(--accent-error); }
  .status-card[data-state="changing"] .status-mark { animation: status-pulse 1.6s var(--motion-ease) infinite; }
  .status-word { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status-sentence { min-width: 0; }
  .status-action { flex: none; margin-left: auto; font-weight: 600; }
  button.status-card { width: 100%; border: 0; font: inherit; font-size: var(--type-xs); text-align: left; cursor: pointer; }
  button.status-card:hover { background-image: var(--interaction-hover); }
  @keyframes status-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  @media (max-width: 720px) { .status-card { margin: 0; border-radius: 0; } }
  @media (prefers-reduced-motion: reduce) {
    .status-slot, .status-card { transition-duration: 1ms; transition-delay: 0ms; }
    .status-card[data-state="changing"] .status-mark { animation: none; }
  }
</style>
