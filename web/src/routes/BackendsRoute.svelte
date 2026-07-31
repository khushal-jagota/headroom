<script lang="ts">
  /** Backend installation, account, update, and provider allowance facts.
   *
   * The backend catalogue is an ordinary screen read. Usage is deliberately separate:
   * acquiring it may spend provider allowance, so no mount, cache invalidation, or
   * catalogue refresh is allowed to call the usage endpoint.
   */
  import { onMount } from "svelte";
  import Button from "../components/Button.svelte";
  import BackendCard from "../components/conversation/BackendCard.svelte";
  import {
    ConversationWireError,
    readBackends,
    refreshBackendUsage,
    updateBackend,
    type BackendSnapshot,
    type BackendUpdateResult,
    type BackendUsageResult,
    type ConversationBackendKey
  } from "../lib/conversation/wire";

  let backends = $state<BackendSnapshot[]>([]);
  let loading = $state(true);
  let refreshingCatalogue = $state(false);
  let catalogueRead: Promise<void> | null = null;
  let catalogueReadRequested = false;
  let catalogueForceRefreshRequested = false;
  let catalogueError = $state<string | null>(null);
  let updatingBackends = $state<Partial<Record<ConversationBackendKey, boolean>>>({});
  let updateResults = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  let usageRefreshing = $state<Partial<Record<ConversationBackendKey, boolean>>>({});
  let usageResults = $state<Partial<Record<ConversationBackendKey, BackendUsageResult>>>({});
  let usageErrors = $state<Partial<Record<ConversationBackendKey, string | null>>>({});

  function sentenceFor(error: unknown): string {
    if (error instanceof ConversationWireError) return error.message;
    return error instanceof Error ? error.message : "The request did not succeed.";
  }

  async function loadBackends(refresh = false): Promise<void> {
    catalogueReadRequested = true;
    catalogueForceRefreshRequested ||= refresh;

    if (catalogueRead === null) {
      loading = backends.length === 0;
      refreshingCatalogue = true;
      catalogueRead = (async () => {
        while (catalogueReadRequested) {
          const forceRefresh = catalogueForceRefreshRequested;
          catalogueReadRequested = false;
          catalogueForceRefreshRequested = false;
          catalogueError = null;
          try {
            backends = await readBackends(forceRefresh);
          } catch (error) {
            catalogueError = sentenceFor(error);
          } finally {
            loading = false;
          }
        }
      })();
      try {
        await catalogueRead;
      } finally {
        catalogueRead = null;
        refreshingCatalogue = false;
      }
      return;
    }

    await catalogueRead;
  }

  async function runBackendUpdate(key: ConversationBackendKey): Promise<void> {
    if (updatingBackends[key]) return;
    updatingBackends = { ...updatingBackends, [key]: true };
    try {
      const result = await updateBackend(key);
      updateResults = { ...updateResults, [key]: result };
      await loadBackends();
    } catch (error) {
      updateResults = {
        ...updateResults,
        [key]: { outcome: "failed", detail: sentenceFor(error), output_tail: "" }
      };
    } finally {
      updatingBackends = { ...updatingBackends, [key]: false };
    }
  }

  async function runUsageRefresh(key: ConversationBackendKey): Promise<void> {
    if (usageRefreshing[key]) return;
    usageRefreshing = { ...usageRefreshing, [key]: true };
    usageErrors = { ...usageErrors, [key]: null };
    try {
      const result = await refreshBackendUsage(key);
      usageResults = { ...usageResults, [key]: result };
    } catch (error) {
      usageErrors = { ...usageErrors, [key]: sentenceFor(error) };
    } finally {
      usageRefreshing = { ...usageRefreshing, [key]: false };
    }
  }

  onMount(() => {
    void loadBackends();
  });
</script>

<section class="backends-page" data-screen="backends">
  <header class="backends-head">
    <div>
      <div class="eyebrow">System</div>
      <h1>Backends</h1>
      <p>What this machine can run, how it is signed in, and the limits providers report.</p>
    </div>
    <Button
      disabled={refreshingCatalogue}
      onclick={() => void loadBackends(true)}
      data-backends-refresh
    >
      {refreshingCatalogue ? "Looking…" : "Look again"}
    </Button>
  </header>

  {#if catalogueError}
    <div class="backends-error" role="alert">
      <span>{catalogueError}</span>
      <Button onclick={() => void loadBackends()}>Retry</Button>
    </div>
  {:else if loading}
    <div class="quiet-line">Loading backends…</div>
  {:else if backends.length === 0}
    <div class="quiet-line">No backends were reported.</div>
  {:else}
    <div class="backends-grid">
      {#each backends as snapshot (snapshot.backend_key)}
        <BackendCard
          {snapshot}
          updating={updatingBackends[snapshot.backend_key] ?? false}
          result={updateResults[snapshot.backend_key] ?? null}
          onUpdate={() => void runBackendUpdate(snapshot.backend_key)}
          usageRefreshing={usageRefreshing[snapshot.backend_key] ?? false}
          usageResult={usageResults[snapshot.backend_key] ?? null}
          usageError={usageErrors[snapshot.backend_key] ?? null}
          onUsageRefresh={() => void runUsageRefresh(snapshot.backend_key)}
        />
      {/each}
    </div>
  {/if}
</section>

<style>
  .backends-page {
    display: grid;
    gap: var(--space-6);
    width: min(100%, 72rem);
    margin: 0 auto;
  }
  .backends-head {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: var(--space-4);
  }
  .backends-head h1 { margin: var(--space-1) 0 0; }
  .backends-head p {
    max-width: 42rem;
    margin: var(--space-2) 0 0;
    color: var(--text-faint);
  }
  .backends-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
    gap: var(--space-4);
    align-items: start;
  }
  .backends-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    color: var(--accent-error);
    font-size: var(--type-sm);
  }
  @media (max-width: 40rem) {
    .backends-head { align-items: start; }
  }
</style>
