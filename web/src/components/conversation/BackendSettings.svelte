<script lang="ts">
  import { onMount } from "svelte";
  import Button from "../Button.svelte";
  import { loadBackendPageSnapshots } from "../../lib/conversation/backendRefresh";
  import {
    updateBackend,
    type BackendSnapshot,
    type BackendUpdateResult,
    type ConversationBackendKey
  } from "../../lib/conversation/wire";

  const backends = [{ key: "claude", label: "Claude" }, { key: "codex", label: "Codex" }] as const;
  let snapshots = $state<BackendSnapshot[]>([]);
  let refreshing = $state(false);
  let refreshError = $state<string | null>(null);
  let updating = $state<Partial<Record<ConversationBackendKey, boolean>>>({});
  let results = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  const anyUpdating = $derived(Object.values(updating).some(Boolean));

  async function refresh() {
    refreshing = true;
    refreshError = null;
    try {
      const result = await loadBackendPageSnapshots(value => { snapshots = value; });
      if (result.snapshots) snapshots = result.snapshots;
      refreshError = result.error;
    } finally {
      refreshing = false;
    }
  }

  async function update(key: ConversationBackendKey) {
    if (updating[key]) return;
    updating[key] = true;
    delete results[key];
    try {
      results[key] = await updateBackend(key);
    } catch (problem) {
      results[key] = {
        outcome: "failed",
        detail: problem instanceof Error ? problem.message : "The update request did not succeed.",
        output_tail: ""
      };
    } finally {
      // An unsuccessful command can still change the installation.
      await refresh();
      updating[key] = false;
    }
  }

  onMount(() => { void refresh(); });
</script>

<section class="backend-settings" aria-label="VPS backends" data-backend-settings>
  <header>
    <h2>VPS backends</h2>
    <Button variant="outline" disabled={refreshing || anyUpdating} onclick={() => void refresh()}>
      {refreshing ? "Checking…" : "Refresh"}
    </Button>
  </header>
  {#if refreshError}<p class="error" role="alert">Could not refresh backend versions. {refreshError}</p>{/if}
  {#each backends as backend}
    {@const snapshot = snapshots.find(item => item.backend_key === backend.key)}
    {@const result = results[backend.key]}
    <article class="backend" data-backend={backend.key} aria-busy={!!updating[backend.key]}>
      <div class="backend-heading">
        <h3>{backend.label}</h3>
        <Button disabled={!!updating[backend.key] || refreshing || !snapshot?.installed || !snapshot.update_advisory?.update_command}
          onclick={() => void update(backend.key)} aria-label={`Update ${backend.label}`}>
          {updating[backend.key] ? "Updating…" : "Update"}
        </Button>
      </div>
      <dl>
        <div><dt>Installed</dt><dd data-installed>{snapshot?.version ?? (snapshot?.installed === false ? "Not installed" : "Unknown")}</dd></div>
        <div><dt>Latest</dt><dd data-latest>{refreshing ? "Checking…" : refreshError ? "Unavailable" : snapshot?.update_advisory?.latest_version ?? "Unavailable"}</dd></div>
      </dl>
      {#if !refreshing && snapshot?.installed && !snapshot.update_advisory?.latest_version && !refreshError}
        <p class="detail">Latest version unavailable.</p>
      {/if}
      {#if !refreshing && snapshot?.installed && !snapshot.update_advisory?.update_command}
        <p class="detail">{snapshot.update_advisory?.detail ?? "This installation needs a manual update."}</p>
      {/if}
      {#if result}
        <p class:error={result.outcome === "failed"} class="result" role={result.outcome === "failed" ? "alert" : "status"}>{result.detail}</p>
        {#if result.outcome === "failed" && result.output_tail}
          <details><summary>Update output</summary><pre>{result.output_tail}</pre></details>
        {/if}
      {/if}
    </article>
  {/each}
</section>

<style>
  .backend-settings { margin-top:var(--space-6); }
  header, .backend-heading { display:flex; align-items:center; justify-content:space-between; gap:var(--space-4); }
  h2, h3 { margin:0; font:600 var(--type-page-section) var(--font-ui); color:var(--tone-strong); }
  .backend { margin-top:var(--space-6); }
  dl { display:flex; flex-wrap:wrap; gap:var(--space-6); margin:var(--space-3) 0 0; }
  dl > div { display:grid; gap:var(--space-1); }
  dt { color:var(--tone-tertiary); font-size:var(--type-small); }
  dd { margin:0; color:var(--tone-primary); font-size:var(--type-body); font-variant-numeric:tabular-nums; }
  .detail, .result, details { margin:var(--space-3) 0 0; font-size:var(--type-small); color:var(--tone-secondary); }
  .result { color:var(--tone-primary); }
  .error { color:var(--accent-error); }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; }
</style>
