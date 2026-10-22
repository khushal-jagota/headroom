<script lang="ts">
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import type { ConnectionStatus } from "../lib/changeStream";
  import { deploymentStatusExpiryDelay, resolveDeploymentStatus } from "../lib/deploymentStatus";
  import { queries } from "../lib/queryCatalogue";
  import { RAIL_MOTION } from "../lib/workspaceRailMotion";

  let { connectionState }: { connectionState: ConnectionStatus } = $props();
  let clock = $state(Date.now());
  const deployment = createQuery(() => queries.deploymentStatus());
  const statusState = $derived(resolveDeploymentStatus(deployment.data, connectionState, clock));
  const statusWord = $derived(
    statusState === "preparing" || statusState === "restarting" ? "Deploying"
      : statusState === "back_up" ? "Deployed"
      : statusState === "problem" ? "Problem"
      : statusState === "reconnecting" ? "Reconnecting" : null
  );
  let displayedWord = $state<string | null>(null);
  let open = $state(false);
  let closing = $state(false);
  let slot = $state<HTMLDivElement | null>(null);
  const motionStyle = `--glide-space:${RAIL_MOTION.space}ms;--glide-fade:${RAIL_MOTION.fadeIn}ms;--glide-delay:${RAIL_MOTION.fadeDelay}ms;--glide-exit:${RAIL_MOTION.exit}ms;--glide-ease:${RAIL_MOTION.ease};--glide-enter:${RAIL_MOTION.enter};--glide-leave:${RAIL_MOTION.leave}`;

  $effect(() => {
    const now = Date.now();
    clock = now;
    const delay = deploymentStatusExpiryDelay(deployment.data, now);
    if (delay === null) return;
    const timer = window.setTimeout(() => { clock = Date.now(); }, delay);
    return () => window.clearTimeout(timer);
  });

  $effect(() => {
    const word = statusWord;
    let obsolete = false;
    let frame: number | undefined;
    let timer: number | undefined;
    if (word !== null) {
      displayedWord = word;
      closing = false;
      // Establish collapsed layout before entrance. Updates retain the card,
      // including a reversal during exit.
      void tick().then(() => {
        if (obsolete) return;
        if (slot) void slot.offsetHeight;
        frame = window.requestAnimationFrame(() => { if (!obsolete) open = true; });
      });
    } else {
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

<div bind:this={slot} class="status-slot" data-shell-status
  data-open={open ? "" : undefined} data-closing={closing ? "" : undefined}
  aria-hidden={statusWord === null ? "true" : undefined} style={motionStyle}>
  <div>
    {#if displayedWord !== null}
      <div class="status-card" data-state={displayedWord === "Problem" ? "wrong" : displayedWord === "Deployed" ? "settled" : "changing"}
        data-connection-status data-connection-state={connectionState} data-status-state={statusState}
        role="status" aria-live="polite">
        <span class="status-mark" aria-hidden="true"></span>
        <span class="status-word">{displayedWord}</span>
      </div>
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
  .status-mark { width: 7px; height: 7px; flex: none; border-radius: var(--radius-pill); background: var(--accent-bright); }
  .status-card[data-state="wrong"] .status-mark { background: var(--accent-error); }
  .status-card[data-state="changing"] .status-mark { animation: status-pulse 1.6s var(--motion-ease) infinite; }
  .status-word { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @keyframes status-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  @media (max-width: 720px) { .status-card { margin: 0; border-radius: 0; } }
  @media (prefers-reduced-motion: reduce) {
    .status-slot, .status-card { transition-duration: 1ms; transition-delay: 0ms; }
    .status-card[data-state="changing"] .status-mark { animation: none; }
  }
</style>
