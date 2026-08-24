<script lang="ts">
  import { onMount } from "svelte";
  import UsageRings from "../components/conversation/UsageRings.svelte";
  import {
    ConversationWireError,
    readBackends,
    refreshBackends,
    setBackendModelEnabled,
    updateBackend,
    type BackendSnapshot,
    type BackendUpdateResult,
    type ConversationBackendKey
  } from "../lib/conversation/wire";

  let backends = $state<BackendSnapshot[]>([]);
  let loading = $state(true);
  let refreshing = $state(false);
  let error = $state<string | null>(null);
  let openBackend = $state<ConversationBackendKey | null>(null);
  let updatingBackends = $state<Partial<Record<ConversationBackendKey, boolean>>>({});
  let updateResults = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  let savingModels = $state<Record<string, boolean>>({});
  let modelErrors = $state<Record<string, string | null>>({});

  function sentenceFor(problem: unknown): string {
    if (problem instanceof ConversationWireError) return problem.message;
    return problem instanceof Error ? problem.message : "The request did not succeed.";
  }

  async function loadBackends(): Promise<void> {
    error = null;
    try {
      backends = await readBackends();
    } catch (problem) {
      error = sentenceFor(problem);
    } finally {
      loading = false;
    }
  }

  async function runRefresh(): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    error = null;
    try {
      const result = await refreshBackends();
      backends = result.backends;
      const failures = result.usage_outcomes
        .filter((outcome) => outcome.outcome === "failed")
        .map((outcome) => outcome.detail)
        .filter((detail): detail is string => detail !== null);
      if (failures.length > 0) error = failures.join(" ");
    } catch (problem) {
      error = sentenceFor(problem);
    } finally {
      refreshing = false;
    }
  }

  async function runBackendUpdate(key: ConversationBackendKey): Promise<void> {
    if (updatingBackends[key]) return;
    updatingBackends = { ...updatingBackends, [key]: true };
    try {
      const result = await updateBackend(key);
      updateResults = { ...updateResults, [key]: result };
      backends = await readBackends();
    } catch (problem) {
      updateResults = {
        ...updateResults,
        [key]: { outcome: "failed", detail: sentenceFor(problem), output_tail: "" }
      };
    } finally {
      updatingBackends = { ...updatingBackends, [key]: false };
    }
  }

  function modelKey(backendKey: ConversationBackendKey, modelId: string): string {
    return `${backendKey}:${modelId}`;
  }

  async function setModelEnabled(
    backendKey: ConversationBackendKey,
    modelId: string,
    enabled: boolean
  ): Promise<void> {
    const key = modelKey(backendKey, modelId);
    if (savingModels[key]) return;
    savingModels = { ...savingModels, [key]: true };
    modelErrors = { ...modelErrors, [key]: null };
    try {
      const result = await setBackendModelEnabled(backendKey, modelId, enabled);
      backends = backends.map((snapshot) => snapshot.backend_key !== backendKey
        ? snapshot
        : {
            ...snapshot,
            available_models: snapshot.available_models.map((model) => model.model_id === modelId
              ? { ...model, enabled: result.enabled }
              : model)
          });
    } catch (problem) {
      modelErrors = { ...modelErrors, [key]: sentenceFor(problem) };
    } finally {
      savingModels = { ...savingModels, [key]: false };
    }
  }

  function observedLabel(snapshot: BackendSnapshot): string | null {
    const observedAt = snapshot.cached_usage?.observed_at;
    if (!observedAt) return null;
    const elapsedMinutes = Math.max(0, Math.floor((Date.now() - Date.parse(observedAt)) / 60_000));
    if (elapsedMinutes < 1) return "read just now";
    if (elapsedMinutes === 1) return "read 1 minute ago";
    if (elapsedMinutes < 60) return `read ${elapsedMinutes} minutes ago`;
    const elapsedHours = Math.floor(elapsedMinutes / 60);
    return `read ${elapsedHours} ${elapsedHours === 1 ? "hour" : "hours"} ago`;
  }

  function oldestObservedLabel(): string | null {
    const snapshots = backends
      .filter((snapshot) => snapshot.cached_usage !== null)
      .sort((left, right) => Date.parse(left.cached_usage!.observed_at) - Date.parse(right.cached_usage!.observed_at));
    return snapshots[0] ? observedLabel(snapshots[0]) : null;
  }

  function isSignedOut(snapshot: BackendSnapshot): boolean {
    return snapshot.identity?.status === "unauthenticated";
  }

  function hasModelUsage(snapshot: BackendSnapshot, modelId: string): boolean {
    return snapshot.cached_usage?.windows.some((window) => window.model_id === modelId) ?? false;
  }

  onMount(() => void loadBackends());
</script>

<section class="backends-page" data-screen="backends">
  <header class="screen-header-row">
    <div class="screen-header-title">
      <div class="eyebrow">System</div>
      <h1>Backends</h1>
    </div>
    <div class="readline">
      {#if oldestObservedLabel()}<span class="when">{oldestObservedLabel()}</span>{/if}
      <button class="quiet-button" disabled={refreshing} onclick={() => void runRefresh()} data-backends-refresh>
        {refreshing ? "Reading…" : "Refresh"}
      </button>
    </div>
  </header>

  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}

  {#if loading}
    <p class="quiet-line">Loading backends…</p>
  {:else if backends.length === 0}
    <p class="quiet-line">No backends were reported.</p>
  {:else}
    <div class="rows">
      {#each backends as snapshot (snapshot.backend_key)}
        {@const open = openBackend === snapshot.backend_key}
        <section class="backend-row" class:open data-conversation-backend={snapshot.backend_key}>
          <div class="rowgrid rowhead">
            <button
              class="opener subject"
              aria-expanded={open}
              aria-label={`${snapshot.backend_key} models`}
              onclick={() => (openBackend = open ? null : snapshot.backend_key)}
            >
              <span class="chevron" aria-hidden="true">{open ? "⌄" : "›"}</span>
              <span class="backend-name">{snapshot.backend_key}</span>
              {#if isSignedOut(snapshot)}
                <span class="identity-note">
                  not signed in
                  {#if snapshot.identity?.login_command}
                    · <b>{snapshot.identity.login_command}</b>
                  {/if}
                </span>
              {/if}
            </button>
            <span class="version-cell">
              {#if snapshot.update_advisory?.update_available && snapshot.update_advisory.update_command}
                <button
                  class="version-action"
                  disabled={updatingBackends[snapshot.backend_key]}
                  onclick={() => void runBackendUpdate(snapshot.backend_key)}
                  data-conversation-backend-update={snapshot.backend_key}
                >
                  {updatingBackends[snapshot.backend_key]
                    ? `updating to ${snapshot.update_advisory.latest_version ?? "latest"}…`
                    : `update ${snapshot.version ?? "unknown"} → ${snapshot.update_advisory.latest_version ?? "latest"}`}
                </button>
              {:else}
                <span class="version">{snapshot.installed ? snapshot.version ?? "version unknown" : "not installed"}</span>
              {/if}
            </span>
            <span class="usage-cell">
              {#if !isSignedOut(snapshot)}
                <UsageRings
                  windows={snapshot.cached_usage?.windows ?? []}
                  label={`Usage allowances for ${snapshot.backend_key}`}
                />
              {/if}
            </span>
          </div>
          {#if open}
            <div class="drawer" data-backend-model-drawer={snapshot.backend_key}>
              {#if snapshot.available_models.length === 0}
                <p class="empty-models">No models were reported.</p>
              {:else}
                {#each snapshot.available_models as model (model.model_id)}
                  {@const key = modelKey(snapshot.backend_key, model.model_id)}
                  <div class="rowgrid model-row" class:off={!model.enabled}>
                    <label class="model-name" for={key}>{model.display_name ?? model.model_id}</label>
                    <span class="model-rings">
                      {#if !isSignedOut(snapshot) && hasModelUsage(snapshot, model.model_id)}
                        <UsageRings
                          windows={snapshot.cached_usage?.windows ?? []}
                          modelId={model.model_id}
                          label={`Usage allowances for ${model.display_name ?? model.model_id}`}
                          compact
                        />
                      {/if}
                    </span>
                    <input
                      id={key}
                      type="checkbox"
                      checked={model.enabled}
                      disabled={savingModels[key]}
                      aria-label={`${model.display_name ?? model.model_id} enabled`}
                      onchange={(event) => void setModelEnabled(
                        snapshot.backend_key,
                        model.model_id,
                        event.currentTarget.checked
                      )}
                    />
                    {#if modelErrors[key]}
                      <span class="model-error" role="alert">{modelErrors[key]}</span>
                    {/if}
                  </div>
                {/each}
              {/if}
            </div>
          {/if}
          {#if updateResults[snapshot.backend_key]}
            {@const result = updateResults[snapshot.backend_key]!}
            <p class="result" class:failed={result.outcome === "failed"}>{result.detail}</p>
          {/if}
        </section>
      {/each}
    </div>
    <p class="note">Refresh re-reads every backend. It spends a little Codex allowance when Codex's reading is stale.</p>
  {/if}
</section>

<style>
  .backends-page { width: min(100%, var(--measure-index)); margin: 0 auto; padding-bottom: var(--space-6); }
  .screen-header-row { display: flex; align-items: flex-start; gap: var(--space-4); }
  .screen-header-title { flex: 1; min-width: 0; }
  .eyebrow { color: var(--text-faint); font-size: var(--type-xs); letter-spacing: var(--tracking-label); text-transform: uppercase; }
  h1 { margin: var(--space-2) 0 0; color: var(--text-strong); font-family: var(--font-serif); font-size: var(--type-serif-display); font-weight: 500; }
  .readline { display: flex; align-items: center; gap: var(--space-3); padding-top: var(--space-2); }
  .when, .version, .version-action { color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs); }
  .quiet-button { padding: var(--space-1) var(--space-3); background: var(--surface-2); border: var(--border-hairline) solid var(--text-faint); border-radius: var(--radius-sm); color: var(--text-strong); cursor: pointer; font: inherit; font-size: var(--type-sm); }
  button:disabled, input:disabled { cursor: default; opacity: .5; }
  .rowgrid { display: grid; grid-template-columns: minmax(0, 1fr) auto 5rem; align-items: center; gap: var(--space-4); }
  .rows { display: grid; gap: var(--space-2); margin-top: var(--space-6); }
  .backend-row { border-radius: var(--radius-md); transition: background var(--motion-fast) var(--motion-ease); }
  .backend-row:hover { background-image: var(--interaction-hover); }
  .backend-row.open { background: var(--surface-recessed); }
  .rowhead { min-height: 4.25rem; padding: var(--space-2) var(--space-3); }
  .opener { margin: 0; padding: 0; background: none; border: 0; color: inherit; cursor: pointer; font: inherit; text-align: left; }
  .subject { display: flex; align-items: baseline; gap: var(--space-3); min-width: 0; }
  .chevron { display: inline-block; width: var(--space-3); color: var(--text-faintest); font-size: var(--type-md); }
  .backend-name { color: var(--text-strong); font-family: var(--font-serif); font-size: var(--type-serif-lg); font-weight: 500; text-transform: capitalize; }
  .rowhead:hover .backend-name, .open .backend-name { color: var(--text-strong); }
  .identity-note { color: var(--text-faintest); font-size: var(--type-xs); }
  .identity-note b { color: var(--text-faint); font-family: var(--font-mono); font-weight: 500; }
  .version-cell { min-width: 0; white-space: nowrap; }
  .usage-cell { display: flex; justify-content: flex-end; min-width: 5rem; }
  .version-action { padding: var(--space-1) var(--space-2); background: var(--surface-2); border: 0; border-radius: var(--radius-sm); color: var(--text-muted); cursor: pointer; letter-spacing: var(--tracking-mono); }
  .drawer { margin: 0 var(--space-3); padding: var(--space-1) 0 var(--space-2) calc(var(--space-3) + var(--space-3)); border-top: var(--border-hairline) solid var(--border-color); }
  .model-row { grid-template-columns: minmax(0, 1fr) auto auto; min-height: 2.5rem; padding: var(--space-2) 0; }
  .model-row + .model-row { border-top: var(--border-hairline) solid var(--border-color); }
  .model-name { min-width: 0; overflow: hidden; color: var(--text-default); font-size: var(--type-sm); text-overflow: ellipsis; white-space: nowrap; }
  .model-row.off .model-name { color: var(--text-faintest); }
  .model-row input { width: 18px; height: 18px; margin: 0; accent-color: var(--accent-bright); cursor: pointer; }
  .model-rings { display: flex; justify-content: flex-end; min-width: 2.75rem; }
  .model-error { grid-column: 1 / -1; color: var(--accent-error); font-size: var(--type-xs); }
  .empty-models, .result, .note, .quiet-line, .error { margin: var(--space-3) 0; color: var(--text-faintest); font-size: var(--type-xs); }
  .result { padding-inline: var(--space-3); }
  .result.failed, .error { color: var(--accent-error); }
  .note { margin-top: var(--space-5); }
  @media (max-width: 720px) {
    .screen-header-row { flex-direction: column; align-items: stretch; gap: var(--space-3); }
    .readline { padding-top: 0; }
    .rowgrid { grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2); }
    .subject { gap: var(--space-2); }
    .version-cell { grid-column: 1; padding-left: calc(var(--space-3) + var(--space-2)); }
    .usage-cell { grid-column: 2; grid-row: 1 / 3; }
    .drawer { margin-inline: var(--space-3); padding-left: calc(var(--space-3) + var(--space-2)); }
    .model-row { grid-template-columns: minmax(0, 1fr) auto auto; }
  }
</style>
