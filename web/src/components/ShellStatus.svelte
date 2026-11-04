<script lang="ts">
  import { onDestroy, tick, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import type { NavigationHealth } from "../lib/navigation/owner";
  import type { ConnectionStatus } from "../lib/changeStream";
  import { queries } from "../lib/queryCatalogue";
  import { releaseState, requestReleaseReload } from "../lib/releaseMonitor";
  import { retryChangeStream } from "../lib/changeStream";
  import UpdateActions from "./UpdateActions.svelte";
  import MacStatus from "./machines/MacStatus.svelte";
  import type { UpdateStatus } from "../lib/updates/contracts";
  import { updatePresentation, type UpdatePresentationState } from "../lib/updates/logic/presentation";
  import { atUpdateDeadline } from "../lib/updates/deadline";
  import { RAIL_MOTION } from "../lib/workspaceRailMotion";

  type Card = { word: string; state: "changing" | "settled" | "wrong" | "done"; action?: string; detail?: string; track?: boolean; sentence?: boolean };
  const CARDS: Partial<Record<UpdatePresentationState, Card>> = {
    available: { word: "Update available", state: "settled", action: "Update" },
    waiting: { word: "Update waits for work to finish", state: "changing", action: "View", sentence: true },
    updating: { word: "Updating Panels", state: "changing", track: true },
    restarting: { word: "Restarting", state: "changing", detail: "Back in a moment", track: true },
    complete: { word: "Update complete", state: "done", action: "Refresh" },
    held: { word: "Refresh waits until this message is safe", state: "changing", sentence: true },
    failed: { word: "Update didn't install", state: "wrong", action: "View" },
    unreachable: { word: "Can't reach Panels", state: "wrong", action: "Try now", detail: "Trying again every few seconds" }
  };
  let { connectionState, navigationHealth = null, status = undefined, presentation = undefined }: {
    connectionState: ConnectionStatus; navigationHealth?: NavigationHealth;
    status?: UpdateStatus; presentation?: UpdatePresentationState;
  } = $props();
  const updates = createQuery(() => ({ ...queries.updates(), enabled: status === undefined }));
  const effectiveStatus = $derived(status ?? updates.data);
  let clock = $state(Date.now());
  const statusState = $derived(presentation ?? updatePresentation(effectiveStatus, connectionState, $releaseState, clock));
  const hasMacRefusal = $derived(Boolean(effectiveStatus?.mac_status?.connection_state === "disconnected" && effectiveStatus.mac_status.refusal));
  const statusKey = $derived(statusState !== "up_to_date" ? statusState : hasMacRefusal ? "mac" : navigationHealth ? "navigation" : null);
  let displayedWord = $state<UpdatePresentationState | "navigation" | "mac" | null>(null);
  let displayedHealth = $state.raw<NavigationHealth>(null);
  let textOut = $state(false);
  let actions = $state<UpdateActions | null>(null);
  const card = $derived(displayedWord === null || (displayedWord === "navigation" || displayedWord === "mac") ? null : CARDS[displayedWord]);
  function activate(): void {
    switch (statusState) {
      case "available": actions?.openDialog("offer"); break;
      case "waiting": actions?.openDialog("waiting"); break;
      case "failed": actions?.openDialog("failed"); break;
      case "complete": requestReleaseReload(); break;
      case "unreachable": retryChangeStream(); break;
    }
  }
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
    const deadlines = [effectiveStatus?.restart_deadline_at ?? effectiveStatus?.deadline_at,
      effectiveStatus?.available?.expires_at].map(value => Date.parse(value ?? "")).filter(value => value > now);
    const cancel = deadlines.map(deadline => atUpdateDeadline(deadline, () => { clock = Date.now(); }));
    return () => { for (const stop of cancel) stop(); };
  });

  $effect(() => {
    const word = statusKey;
    const health = navigationHealth;
    let obsolete = false;
    let frame: number | undefined;
    let timer: number | undefined;
    if (word !== null) {
      // A swap between two open occupants owns its own distance. An entrance does not:
      // the row glide already carries it.
      const replacing = untrack(() => open && displayedWord !== null && (displayedWord !== word || displayedHealth !== health));
      const previousHeight = replacing && slot ? slot.getBoundingClientRect().height : null;
      // A return during the collapse keeps the size the collapse had reached.
      const returningHeight = untrack(() =>
        !replacing && !open && resize !== null && slot ? slot.getBoundingClientRect().height : null);
      const applyWord = async () => {
        if (obsolete) return;
        // The row can still move during the text fade. Start from its current
        // rendered height, rather than the height from before that fade.
        const handoverHeight = previousHeight !== null && slot ? slot.getBoundingClientRect().height : null;
        displayedWord = word;
        displayedHealth = health;
        textOut = false;
        closing = false;
        await tick();
        if (!obsolete && handoverHeight !== null) takeHeight(handoverHeight);
      };
      if (replacing && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        textOut = true;
        timer = window.setTimeout(() => { void applyWord(); },
          parseFloat(getComputedStyle(slot!).getPropertyValue("--motion-fast")) || 90);
      } else { void applyWord(); }
      // Establish collapsed layout before entrance. Updates retain the card,
      // including a reversal during exit.
      void tick().then(() => {
        if (obsolete) return;
        if (previousHeight !== null) return;
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
        displayedHealth = null;
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

<div bind:this={slot} class="status-slot" data-shell-status
  data-open={open ? "" : undefined} data-closing={closing ? "" : undefined}
  aria-hidden={statusKey === null ? "true" : undefined} style={motionStyle}>
  <div role="status" aria-live="polite" inert={closing ? true : undefined}>
    {#if displayedWord}
      <div class="status-card" class:mac-only={displayedWord === "mac" && !displayedHealth} data-state={card?.state ?? (displayedHealth?.state === "degraded" ? "wrong" : "changing")}
        data-connection-status data-connection-state={connectionState} data-status-state={statusState}
        >
        {#if card}
          <button type="button" class="status-update" aria-disabled={!card.action ? "true" : undefined} tabindex={card.action ? 0 : -1}
            data-shell-refresh={displayedWord === "complete" ? "" : undefined} onclick={activate}>
          <span class="status-base">
            <span class="status-mark" aria-hidden="true"></span>
            <span class:status-sentence={card.sentence} class:status-word={!card.sentence} class="swap" data-out={textOut ? "" : undefined}>{card.word}</span>
            {#if card.action}<span class="swap status-action" data-out={textOut ? "" : undefined}>{card.action}</span>{/if}
          </span>
          {#if card.track}<span class="status-track" aria-hidden="true"><i></i></span>{/if}
          {#if card.detail}<span class="swap status-detail" data-out={textOut ? "" : undefined}>{card.detail}</span>{/if}
          </button>
        {/if}
        <MacStatus health={effectiveStatus?.mac_status} />
        {#if displayedHealth}
          <span class="navigation-health" data-navigation-health={displayedHealth.state} data-state={displayedHealth.state === "degraded" ? "wrong" : "changing"}>
            <span class="status-mark" aria-hidden="true"></span>
            <span class="navigation-health-text"><span>Navigation {displayedHealth.state}</span>
              {#if displayedHealth.state === "degraded" && displayedHealth.availability.reason !== null}<span class="navigation-reason">{displayedHealth.availability.reason}</span>{/if}
            </span>
          </span>
        {/if}
      </div>
    {/if}
  </div>
</div>
{#if effectiveStatus}<UpdateActions bind:this={actions} status={effectiveStatus} now={clock} />{/if}


<style>
  .status-slot { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--glide-space) var(--glide-ease); }
  .status-slot > div { min-height: 0; overflow: hidden; }
  .status-slot[data-open] { grid-template-rows: 1fr; }
  .status-slot[data-closing] { grid-template-rows: 0fr; transition-delay: var(--glide-exit); }
  .status-card {
    display: flex; flex-direction: column; align-items: stretch; gap: var(--space-2); width: 100%; border: 0; font: inherit; text-align: left; padding: var(--space-2); margin-bottom: var(--space-2);
    border-radius: var(--radius-sm); background: var(--accent-surface); color: var(--accent-text); font-size: var(--type-xs);
    opacity: 0; transform: translateY(var(--space-1));
    transition: opacity var(--glide-fade) var(--glide-enter) var(--glide-delay), transform var(--glide-fade) var(--glide-enter) var(--glide-delay), background-color var(--motion-slow) var(--motion-ease);
  }
  .status-slot[data-open] .status-card { opacity: 1; transform: none; }
  .status-slot[data-closing] .status-card {
    opacity: 0; transform: translateY(calc(var(--space-1) * -1));
    transition: opacity var(--glide-exit) var(--glide-leave), transform var(--glide-exit) var(--glide-leave);
  }
  .status-card.mac-only { padding: 0; margin-bottom: 0; border-radius: 0; background: transparent; }
  .status-card[data-state="wrong"] { background: var(--error-surface); color: var(--text-strong); }
  .status-update:not([aria-disabled]) { cursor: pointer; }
  .status-update:not([aria-disabled]):hover { background-image: var(--interaction-hover); }
  .status-update:focus-visible { outline: var(--border-strong) solid var(--accent-bright); outline-offset: calc(-1 * var(--border-strong)); }
  .status-update { display: flex; flex-direction: column; gap: var(--space-2); width: 100%; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; }
  .status-card[data-state="done"] .status-mark { background: var(--accent-done); }
  .swap { transition: opacity var(--motion-fast) var(--motion-ease); }
  .swap[data-out] { opacity: 0; }
  .status-track { display: block; height: var(--space-1); border-radius: var(--radius-pill); background: var(--surface-2); overflow: hidden; }
  .status-track i { display: block; width: 40%; height: 100%; border-radius: var(--radius-pill); background: var(--accent-bright); animation: track 1.6s var(--glide-ease) infinite; }
  @keyframes track { from { transform: translateX(-100%); } to { transform: translateX(250%); } }
  .status-detail { flex-basis: 100%; }
  .status-base { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
  .navigation-health { display: flex; align-items: baseline; gap: var(--space-2); min-width: 0; }
  .navigation-health .status-mark { align-self: start; margin-top: .4em; }
  .navigation-health-text { min-width: 0; overflow-wrap: anywhere; }
  .navigation-reason { display: block; white-space: pre-wrap; }
  .navigation-health[data-state="wrong"] .status-mark { background: var(--accent-error); }
  .status-mark { width: 7px; height: 7px; flex: none; border-radius: var(--radius-pill); background: var(--accent-bright); }
  .status-card[data-state="wrong"] .status-mark { background: var(--accent-error); }
  .status-card[data-state="changing"] .status-mark { animation: status-pulse 1.6s var(--motion-ease) infinite; }
  .navigation-health[data-state="changing"] .status-mark { background: var(--accent-bright); animation: status-pulse 1.6s var(--motion-ease) infinite; }
  .status-word { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status-sentence { min-width: 0; }
  .status-action { flex: none; margin-left: auto; font-weight: 600; }
  @keyframes status-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  @media (max-width: 720px) {
    .status-card { margin: 0; border-radius: 0; padding: var(--space-3); }
    .status-card.mac-only { padding: var(--space-3); }
  }
  @media (prefers-reduced-motion: reduce) {
    .status-slot, .status-card, .swap { transition-duration: 0ms; transition-delay: 0ms; }
    .status-card[data-state="changing"] .status-mark, .navigation-health[data-state="changing"] .status-mark, .status-track i { animation: none; }
  }
</style>
