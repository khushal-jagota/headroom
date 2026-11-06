<script lang="ts">
  import { onDestroy, tick, untrack } from "svelte";
  import type { NavigationHealth } from "../lib/navigation/owner";
  import type { ConnectionStatus } from "../lib/changeStream";
  import { requestReleaseReload } from "../lib/releaseMonitor";
  import { retryChangeStream } from "../lib/changeStream";
  import UpdateActions from "./UpdateActions.svelte";
  import type { UpdateStatus } from "../lib/updates/contracts";
  import { updateCard, type UpdateCardState } from "../lib/updates/logic/presentation";
  import { RAIL_MOTION } from "../lib/workspaceRailMotion";

  let { connectionState, navigationHealth = null, status, presentation, now }: {
    connectionState: ConnectionStatus; navigationHealth?: NavigationHealth;
    status: UpdateStatus | undefined; presentation: UpdateCardState | null; now: number;
  } = $props();
  // An update restarts Panels, which drops navigation for a moment. That is part of the
  // one "Updating…" wait, not a second thing to report.
  const shownHealth = $derived(presentation === "updating" ? null : navigationHealth);
  const statusKey = $derived(presentation ?? (shownHealth ? "navigation" : null));
  let displayedWord = $state<UpdateCardState | "navigation" | null>(null);
  let displayedHealth = $state.raw<NavigationHealth>(null);
  let textOut = $state(false);
  let actions = $state<UpdateActions | null>(null);
  // The card follows the displayed word, so its text changes with the crossfade.
  const card = $derived(displayedWord === null || displayedWord === "navigation" ? null : updateCard(displayedWord, status));
  function activate(): void {
    if (!card?.action) return;
    switch (card.action.does) {
      case "offer": actions?.openDialog("offer"); break;
      case "waiting": actions?.openDialog("waiting"); break;
      case "refresh": requestReleaseReload(); break;
      case "reconnect": retryChangeStream(); break;
      case "retry": void actions?.perform({ action: "retry" }, "failed"); break;
      case "retry_mac": if (card.info) void actions?.perform({ action: "retry_mac" }, card.info); break;
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
    const word = statusKey;
    const health = shownHealth;
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
      <div class="status-card" data-state={card?.tone ?? "changing"}
        data-connection-status data-connection-state={connectionState} data-status-state={presentation ?? "none"}>
        {#if card}
          <span class="status-base">
            <span class="status-mark" aria-hidden="true"></span>
            <span class:status-sentence={card.sentence} class:status-word={!card.sentence} class="swap" data-out={textOut ? "" : undefined}>{card.word}</span>
            {#if card.action}
              <button type="button" class="swap status-action" data-out={textOut ? "" : undefined}
                data-shell-refresh={card.action.does === "refresh" ? "" : undefined} onclick={activate}>{card.action.label}</button>
            {/if}
            {#if card.info}
              <button type="button" class="swap status-info" class:status-info--alone={!card.action} aria-label="Update details" data-out={textOut ? "" : undefined}
                data-update-info={card.info} onclick={() => card?.info && actions?.openDialog(card.info)}>i</button>
            {/if}
          </span>
          {#if card.track}<span class="status-track" aria-hidden="true"><i></i></span>{/if}
          {#if card.detail}<span class="swap status-detail" data-out={textOut ? "" : undefined}>{card.detail}</span>{/if}
        {/if}
        {#if displayedHealth}
          <span class="navigation-health" data-navigation-health={displayedHealth.state} data-state={"changing"}>
            <span class="status-mark" aria-hidden="true"></span>
            <span class="navigation-health-text"><span>Navigation {displayedHealth.state}</span>
            </span>
          </span>
        {/if}
      </div>
    {/if}
  </div>
</div>
{#if status}<UpdateActions bind:this={actions} {status} {now} />{/if}


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
  .status-card[data-state="wrong"] { background: var(--error-surface); color: var(--text-strong); }
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
  .status-mark { width: 7px; height: 7px; flex: none; border-radius: var(--radius-pill); background: var(--accent-bright); }
  .status-card[data-state="wrong"] .status-mark { background: var(--accent-error); }
  .status-card[data-state="changing"] .status-mark { animation: status-pulse 1.6s var(--motion-ease) infinite; }
  .navigation-health[data-state="changing"] .status-mark { background: var(--accent-bright); animation: status-pulse 1.6s var(--motion-ease) infinite; }
  .status-word { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status-sentence { min-width: 0; }
  .status-action { flex: none; margin-left: auto; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; font-weight: 660; cursor: pointer; }
  .status-action:hover { text-decoration: underline; }
  .status-info { flex: none; width: 16px; height: 16px; margin-left: var(--space-1); padding: 0; border: var(--border-hairline) solid currentColor; border-radius: var(--radius-pill); background: transparent; color: inherit; font: 600 10px/14px var(--font-ui); opacity: .75; cursor: pointer; }
  .status-info--alone { margin-left: auto; }
  .status-info:hover { opacity: 1; }
  .status-action:focus-visible, .status-info:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: 2px; }
  @keyframes status-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  @media (max-width: 720px) {
    .status-card { margin: 0; border-radius: 0; padding: var(--space-3); }
  }
  @media (prefers-reduced-motion: reduce) {
    .status-slot, .status-card, .swap { transition-duration: 0ms; transition-delay: 0ms; }
    .status-card[data-state="changing"] .status-mark, .navigation-health[data-state="changing"] .status-mark, .status-track i { animation: none; }
  }
</style>
