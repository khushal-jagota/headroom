<script lang="ts">
  import { onMount, tick } from "svelte";
  import { createQuery, useQueryClient } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import UsageHeader from "../components/usage/UsageHeader.svelte";
  import AllowanceWindows from "../components/usage/AllowanceWindows.svelte";
  import RecordedUsage from "../components/usage/RecordedUsage.svelte";
  import ConfigRoute from "./ConfigRoute.svelte";
  import type { ConfigSelection } from "../lib/shellRouting";
  import type { UsageTab } from "../lib/usageAddress";
  import { usageBackends } from "../components/usage/usageProviders";
  import { refreshRecordedUsage, type UsageDays, type UsageProvider, type UsageSummary } from "../lib/conversation/recordedUsageWire";
  import { timestampLabel } from "../components/usage/usagePresentation";
  import UsageRings from "../components/conversation/UsageRings.svelte";
  import { backendIdentityNote, type BackendIdentityNote } from "../lib/conversation/backendIdentity";
  import {
    BACKENDS_PAGE_SURROUNDINGS,
    backendCanBeSignedIn,
    signInCaveat,
    signInIsPending,
    signInOutcome
  } from "../lib/conversation/backendSignIn";
  import {
    BackendSignInSession,
    type SignInPageState
  } from "../lib/conversation/backendSignInSession";
  import {
    loadBackendPageSnapshots,
    refreshBackendSnapshots
  } from "../lib/conversation/backendRefresh";
  import {
    cancelBackendSignIn,
    ConversationWireError,
    readBackendSignIn,
    readBackends,
    startBackendSignIn,
    setBackendModelEnabled,
    updateBackend,
    type BackendSnapshot,
    type BackendUpdateResult,
    type ConversationBackendKey
  } from "../lib/conversation/wire";

  let { tab = "limits", roleKind = "index", roleId }: {
    tab?: UsageTab;
    roleKind?: ConfigSelection["roleKind"];
    roleId?: string;
  } = $props();
  let metric = $derived(tab === "config" ? "limits" : tab);
  let days = $state<UsageDays>(1);
  let providerSelection = $state<string[] | null>(null);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const queryClient = useQueryClient();
  const recorded = createQuery(() => ({
    ...queries.recordedUsage(days, timezone), enabled: tab === "tokens" || tab === "cost", retry: false
  }));
  let lastUsage = $state<UsageSummary | null>(null);
  $effect(() => { if (recorded.data) lastUsage = recorded.data; });
  let summary = $derived(recorded.data ?? lastUsage);
  let nativeRefreshError = $state<string | null>(null);
  let allowanceNow = $state(Date.now());
  let backends = $state<BackendSnapshot[]>([]);
  let supportedBackends = $derived(usageBackends(backends));
  let providerKeys = $derived(metric === "limits"
    ? supportedBackends.map(backend => backend.backend_key)
    : [...new Set([...(summary?.sources.map(source => source.provider) ?? []), ...(summary?.providers.map(row => row.provider) ?? [])])]);
  let selectedProviders = $derived(providerSelection === null ? providerKeys : providerKeys.filter(provider => providerSelection?.includes(provider)));
  let recordedProviders = $derived(selectedProviders.filter((provider): provider is UsageProvider => provider === "codex" || provider === "claude"));
  let visibleBackends = $derived(supportedBackends.filter(backend => selectedProviders.includes(backend.backend_key)));
  let loading = $state(true);
  let refreshing = $state(false);
  let error = $state<string | null>(null);
  let openBackend = $state<ConversationBackendKey | null>(null);
  let updatingBackends = $state<Partial<Record<ConversationBackendKey, boolean>>>({});
  let updateResults = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  let savingModels = $state<Record<string, boolean>>({});
  let modelErrors = $state<Record<string, string | null>>({});
  // Only codex can be signed in, so there is one session controller rather than a map.
  const SIGN_IN_BACKEND: ConversationBackendKey = "codex";
  let signInState = $state<SignInPageState>({ signIn: null, busy: false, error: null });
  const signInSession = new BackendSignInSession(
    {
      start: startBackendSignIn,
      read: readBackendSignIn,
      cancel: cancelBackendSignIn,
      reloadCards: () => loadBackends(),
      setTimer: (run, ms) => setTimeout(run, ms),
      clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)
    },
    (next) => {
      signInState = next;
    }
  );

  function sentenceFor(problem: unknown): string {
    if (problem instanceof ConversationWireError) return problem.message;
    return problem instanceof Error ? problem.message : "The request did not succeed.";
  }

  async function loadBackends(): Promise<void> {
    error = null;
    try {
      const result = await loadBackendPageSnapshots(async (ordinarySnapshots) => {
        backends = ordinarySnapshots;
        loading = false;
        await tick();
      });
      if (result.snapshots !== null) backends = result.snapshots;
      error = result.error;
    } finally {
      loading = false;
    }
  }

  async function runRefresh(): Promise<void> {
    if (refreshing) return;
    refreshing = true;
    error = null;
    nativeRefreshError = null;
    // Neither source waits for the other, and either keeps its previous good reading.
    await Promise.allSettled([
      (async () => {
        const result = await refreshBackendSnapshots();
        if (result.snapshots !== null) backends = result.snapshots;
        error = result.error;
        allowanceNow = Date.now();
      })(),
      (async () => {
        try {
          await refreshRecordedUsage();
          await queryClient.invalidateQueries({ queryKey: ["recorded-usage"], refetchType: "none" });
          await recorded.refetch();
        } catch (problem) {
          nativeRefreshError = sentenceFor(problem);
        }
      })()
    ]);
    refreshing = false;
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

  function isSignedOut(snapshot: BackendSnapshot): boolean {
    return snapshot.identity?.status === "unauthenticated";
  }

  /** The card's one identity line, as nothing or as itself: a list of at most one, so
   *  the markup reads it once. */
  function identityNotes(snapshot: BackendSnapshot): BackendIdentityNote[] {
    const note = backendIdentityNote(snapshot);
    return note ? [note] : [];
  }

  function hasModelUsage(snapshot: BackendSnapshot, modelId: string): boolean {
    return snapshot.cached_usage?.windows.some((window) => window.model_id === modelId) ?? false;
  }

  let mounted = $state(false);
  let limitsLoaded = false;
  onMount(() => { mounted = true; });
  $effect(() => {
    if (!mounted || tab !== "limits") return;
    if (!limitsLoaded) {
      limitsLoaded = true;
      void loadBackends();
    }
    // An initial Config visit does not read backend metadata. The first Limits visit
    // still reads cached facts. Every Limits entry resumes any existing sign-in.
    void signInSession.enter(SIGN_IN_BACKEND);
    // Leaving Limits stops polling without cancelling a device sign-in on the server.
    return () => signInSession.leave();
  });
</script>

<section class="backends-page" data-screen="backends">
  <UsageHeader {tab} {days} {providerKeys} {selectedProviders} busy={refreshing}
    onDays={value => days = value}
    onProviders={value => providerSelection = value} onRefresh={() => void runRefresh()} />

  {#if tab === "config"}
    {#key `${roleKind}/${roleId ?? ""}`}
      <ConfigRoute {roleKind} {roleId} baseHref="#/backends/config" />
    {/key}
  {:else}
  {#if nativeRefreshError}<p class="error" role="alert">Local usage: {nativeRefreshError}</p>{/if}
  {#if metric !== "limits"}
    {#if recorded.error}<p class="error" role="alert">Local usage: {sentenceFor(recorded.error)}{summary ? " The previous reading remains below." : ""}</p>{/if}
    {#if recorded.isFetching}<p class="quiet-line" role="status">Reading stored usage…</p>{/if}
    {#if summary}
      <div aria-busy={recorded.isFetching}><RecordedUsage {summary} providers={recordedProviders} {metric} /></div>
    {:else if !recorded.isFetching && !recorded.error}<p class="quiet-line">No stored usage was reported.</p>{/if}
  {:else}
  {#if error}<p class="error" role="alert">Allowances: {error}</p>{/if}

  {#if loading}
    <p class="quiet-line">Loading backends…</p>
  {:else if supportedBackends.length === 0}
    <p class="quiet-line">No backends were reported.</p>
  {:else}
    <div class="rows">
      {#each visibleBackends as snapshot (snapshot.backend_key)}
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
              {#if snapshot.cached_usage}<span class="observed">Observed {timestampLabel(snapshot.cached_usage.observed_at)}</span>{/if}
              {#each identityNotes(snapshot) as note (note.text)}
                <span class="identity-note" data-backend-identity-note>
                  {note.text}
                  {#if note.loginCommand && !backendCanBeSignedIn(snapshot)}
                    · <b>{note.loginCommand}</b>
                  {/if}
                </span>
              {/each}
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
          </div>
          {#if !isSignedOut(snapshot)}
            <AllowanceWindows windows={snapshot.cached_usage?.windows ?? []}
              observedAt={snapshot.cached_usage?.observed_at ?? null} now={allowanceNow} />
          {/if}
          {#if backendCanBeSignedIn(snapshot)}
            {@const signIn = signInState.signIn}
            {@const outcome = signInOutcome(signIn)}
            <div class="sign-in" data-backend-sign-in={snapshot.backend_key}>
              {#if signInIsPending(signIn) && signIn}
                <p class="sign-in-step">
                  1. Open
                  <a href={signIn.verification_url ?? "#"} target="_blank" rel="noreferrer noopener"
                    data-backend-sign-in-url>{signIn.verification_url}</a>
                  and sign in.
                </p>
                <p class="sign-in-step">
                  2. Enter this one-time code:
                  <b data-backend-sign-in-code>{signIn.user_code}</b>
                </p>
                <p class="sign-in-caveat">
                  Panels stops showing this code after its own time limit. It cannot promise
                  the code stays valid until then.
                </p>
                <button
                  class="sign-in-action"
                  disabled={signInState.busy}
                  onclick={() => void signInSession.cancel(snapshot.backend_key)}
                  data-backend-sign-in-cancel={snapshot.backend_key}
                >Cancel sign-in</button>
              {:else}
                {@const caveat = signInCaveat(snapshot, BACKENDS_PAGE_SURROUNDINGS)}
                {#if caveat}
                  <p class="sign-in-caveat" data-backend-sign-in-caveat>{caveat}</p>
                {/if}
                <button
                  class="sign-in-action"
                  disabled={signInState.busy}
                  onclick={() => void signInSession.start(snapshot.backend_key)}
                  data-backend-sign-in-start={snapshot.backend_key}
                >{signInState.busy ? "Starting…" : "Sign in"}</button>
              {/if}
              {#if outcome}
                <p class="sign-in-outcome" data-backend-sign-in-outcome>{outcome}</p>
              {/if}
              {#if signInState.error}
                <p class="error" role="alert">{signInState.error}</p>
              {/if}
            </div>
          {/if}
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
    {#if !visibleBackends.length}<p class="quiet-line">Select a backend to view its allowances.</p>{/if}
  {/if}
  {/if}
  {/if}
</section>

<style>
  .backends-page { width: min(100%, calc(var(--measure-index) + var(--space-page-tail) + var(--space-page-tail))); margin: 0 auto; padding-bottom: var(--space-6); }
  .version, .version-action { color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs); }
  :is(button,input):focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
  button:disabled, input:disabled { cursor: default; opacity: .5; }
  .rowgrid { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--space-4); }
  .rows { display: grid; gap: var(--space-6); }
  .backend-row { border-radius: var(--radius-md); transition: background var(--motion-fast) var(--motion-ease); }
  .backend-row .rowhead:hover { background-image: var(--interaction-hover); }
  .backend-row.open { background: var(--surface-recessed); }
  .rowhead { min-height: 4.25rem; padding: var(--space-2) 0; }
  .opener { margin: 0; padding: 0; background: none; border: 0; color: inherit; cursor: pointer; font: inherit; text-align: left; }
  .subject { display: flex; align-items: baseline; flex-wrap:wrap; gap: var(--space-2); min-width: 0; }
  .observed { color:var(--text-faintest); font-size:var(--type-xs); }
  .chevron { display: inline-block; width: var(--space-3); color: var(--text-faintest); font-size: var(--type-md); }
  .backend-name { color: var(--text-strong); font-family: var(--font-ui); font-size: var(--type-sm); font-weight: 600; text-transform: capitalize; }
  .rowhead:hover .backend-name, .open .backend-name { color: var(--text-strong); }
  .sign-in { padding: var(--space-3) 0 var(--space-1); display: flex; flex-direction: column; gap: var(--space-2); align-items: flex-start; }
  .sign-in-step { margin: 0; font-size: var(--type-sm); color: var(--text-default); }
  .sign-in-step a { overflow-wrap:anywhere; }
  .sign-in-step b { font-family: var(--font-mono); color: var(--accent-text); letter-spacing: 0.06em; }
  .sign-in-caveat { margin: 0; font-size: var(--type-xs); color: var(--text-faint); max-width: 56ch; }
  .sign-in-outcome { margin: 0; font-size: var(--type-xs); color: var(--text-faint); }
  .sign-in-action { font: inherit; font-size: var(--type-xs); cursor: pointer; background: var(--surface-2); color: var(--text-default); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); padding: var(--space-1) var(--space-3); }
  .sign-in-action:disabled { cursor: default; color: var(--text-faintest); }
  .identity-note { color: var(--text-faintest); font-size: var(--type-xs); }
  .identity-note b { color: var(--text-faint); font-family: var(--font-mono); font-weight: 500; }
  .version-cell { min-width: 0; white-space: normal; text-align:right; overflow-wrap:anywhere; }
  .version-action { padding: var(--space-1) var(--space-2); background: var(--surface-2); border: 0; border-radius: var(--radius-sm); color: var(--text-muted); cursor: pointer; letter-spacing: var(--tracking-mono); }
  .drawer { margin: 0 var(--space-3); padding: var(--space-1) 0 var(--space-2) calc(var(--space-3) + var(--space-3)); border-top: var(--border-hairline) solid var(--border-color); }
  .model-row { grid-template-columns: minmax(0, 1fr) auto auto; min-height: 2.5rem; padding: var(--space-2) 0; }
  .model-row + .model-row { border-top: var(--border-hairline) solid var(--border-color); }
  .model-name { min-width: 0; overflow: hidden; color: var(--text-default); font-size: var(--type-sm); text-overflow: ellipsis; white-space: nowrap; }
  .model-row.off .model-name { color: var(--text-faintest); }
  .model-row input { width: 18px; height: 18px; margin: 0; accent-color: var(--accent-bright); cursor: pointer; }
  .model-rings { display: flex; justify-content: flex-end; min-width: 2.75rem; }
  .model-error { grid-column: 1 / -1; color: var(--accent-error); font-size: var(--type-xs); }
  .empty-models, .result, .quiet-line, .error { margin: var(--space-3) 0; color: var(--text-faintest); font-size: var(--type-xs); }
  .result { padding-inline: var(--space-3); }
  .result.failed, .error { color: var(--accent-error); }
  @media (max-width: 720px) {
    .rowgrid { grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2); }
    .subject { gap: var(--space-2); }
    .version-cell { grid-column: 1 / -1; text-align:left; padding-left: calc(var(--space-3) + var(--space-2)); }
    .drawer { margin-inline: var(--space-3); padding-left: calc(var(--space-3) + var(--space-2)); }
    .model-row { grid-template-columns: minmax(0, 1fr) auto auto; }
  }
</style>
