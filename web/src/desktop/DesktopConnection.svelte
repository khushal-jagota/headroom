<script lang="ts">
  import { get } from "svelte/store";
  import { connectionStatus, retryChangeStream } from "../lib/changeStream";
  import { desktopBridge } from "./bridge";
  import { queryClient } from "../lib/queryClient";

  let failed = $state(false);
  let dismissed = $state(false);
  let address = $state("");
  let busy = $state(false);

  async function probe(retry = false): Promise<void> {
    busy = true;
    try {
      const response = await fetch("/api/meta", { signal: AbortSignal.timeout(5_000) });
      if (get(connectionStatus) === "connected") { failed = false; return; }
      failed = !response.ok;
      if (!failed) {
        dismissed = false;
        // The automatic probe only observes. Replacing the initial live stream here
        // can leave a forwarded stream open and consume another server connection.
        if (retry) {
          retryChangeStream();
          void queryClient.refetchQueries({ type: "active" });
        }
      }
    } catch { failed = get(connectionStatus) !== "connected"; }
    finally { busy = false; }
  }

  $effect(() => {
    if ($connectionStatus === "connected") { failed = false; dismissed = false; }
    else { void desktopBridge().storedServerAddress().then(value => address = value ?? ""); void probe(); }
  });
</script>

{#if failed && !dismissed}
  <section class="offline" aria-label="Server connection" data-desktop-offline>
    <div class="offline-copy"><h1>Cannot reach the server</h1><p>{address}</p><p>Your last session stays here. Changes need a connection.</p></div>
    <div class="offline-actions"><button type="button" onclick={() => probe(true)} disabled={busy}>{busy ? "Checking…" : "Retry"}</button><a href="panels://setup/">Change server</a><button type="button" class="quiet" onclick={() => dismissed = true}>View last session</button></div>
  </section>
{/if}

<style>
  .offline { position: fixed; z-index: 100; inset: auto var(--space-5) var(--space-5); display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-5); padding: var(--space-5); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-md); background: var(--surface-raised); color: var(--text-strong); box-shadow: var(--shadow-float); }
  h1 { font-size: var(--type-body); font-weight: 600; margin: 0 0 var(--space-2); }
  p { font-size: var(--type-small); color: var(--text-muted); margin: var(--space-1) 0; overflow-wrap: anywhere; }
  .offline-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-4); }
  button, a { font: inherit; font-size: var(--type-small); color: var(--accent-text); }
  button { border: 0; border-radius: var(--radius-sm); background: var(--accent-surface); padding: var(--space-2) var(--space-3); cursor: pointer; }
  .quiet { padding: 0; background: transparent; color: var(--text-muted); }
</style>
