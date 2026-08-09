<script lang="ts">
  import { onMount } from "svelte";
  import {
    createReleaseMonitor,
    readBootAppSha,
    type ReleaseSnapshot
  } from "../lib/releaseMonitor";

  let release = $state<ReleaseSnapshot>({
    updateAvailable: false,
    compositionActive: false,
    reloadRequested: false
  });
  let monitor: ReturnType<typeof createReleaseMonitor> | null = null;

  function requestReload(): void {
    monitor?.requestReload();
  }

  onMount(() => {
    const releaseMonitor = createReleaseMonitor({
      bootSha: readBootAppSha(document),
      document,
      window
    });
    monitor = releaseMonitor;
    const unsubscribe = releaseMonitor.subscribe((value) => {
      release = value;
    });
    releaseMonitor.start();
    return () => {
      unsubscribe();
      releaseMonitor.stop();
      if (monitor === releaseMonitor) monitor = null;
    };
  });
</script>

{#if release.updateAvailable}
  <aside class="release-update" aria-live="polite" data-release-update>
    <span>
      {release.reloadRequested && release.compositionActive
        ? "Your update will apply when this message is safe."
        : "A new Panels version is ready."}
    </span>
    <button
      type="button"
      onclick={requestReload}
      disabled={release.reloadRequested && release.compositionActive}
    >
      {release.reloadRequested && release.compositionActive ? "Waiting" : "Update now"}
    </button>
  </aside>
{/if}

<style>
  .release-update {
    position: fixed;
    z-index: 80;
    right: max(1rem, env(safe-area-inset-right));
    bottom: max(1rem, env(safe-area-inset-bottom));
    display: flex;
    align-items: center;
    gap: var(--space-3);
    max-width: min(28rem, calc(100vw - 2rem));
    padding: var(--space-3) var(--space-3) var(--space-3) var(--space-4);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-lg);
    background: var(--surface-raised);
    color: var(--text-strong);
    box-shadow: 0 var(--space-3) var(--space-6) var(--surface-scrim);
    font-size: 0.9rem;
  }

  .release-update button {
    flex: none;
    min-height: 2.25rem;
    padding: 0 var(--space-3);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-md);
    background: var(--accent-text);
    color: var(--accent-ink);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }

  .release-update button:disabled {
    opacity: 0.55;
    cursor: default;
  }

  @media (max-width: 720px) {
    .release-update {
      right: 1rem;
      bottom: calc(4.75rem + env(safe-area-inset-bottom));
      left: 1rem;
      max-width: none;
    }
  }
</style>
