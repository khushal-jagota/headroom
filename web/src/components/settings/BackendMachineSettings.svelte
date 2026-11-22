<script lang="ts">
  import type { Snippet } from "svelte";
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import Button from "../Button.svelte";
  import BackendSignInBlock from "../conversation/BackendSignInBlock.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import { backendStatus } from "../../lib/settings/statusText";
  import { loadBackendPageSnapshots } from "../../lib/conversation/backendRefresh";
  import { backendCanBeSignedIn, signInIsPending } from "../../lib/conversation/backendSignIn";
  import { BackendSignInSession, type SignInPageState } from "../../lib/conversation/backendSignInSession";
  import { backendLabel } from "../../lib/drivers/presentation";
  import { queries } from "../../lib/queryCatalogue";
  import {
    cancelBackendSignIn,
    readBackendSignIn,
    readBackends,
    startBackendSignIn,
    submitBackendSignInReturnUrl,
    updateBackend,
    type BackendSnapshot,
    type BackendUpdateResult,
    type ConversationBackendKey
  } from "../../lib/conversation/wire";

  /* Backends: one row per backend Panels updates, then any other it can sign in. A row says
     what is installed and what is current, and offers an update or a sign-in. */
  let { machineId, isHome, machineChoice }: { machineId: string; isHome: boolean; machineChoice: Snippet } = $props();
  const drivers = createQuery(() => queries.drivers());
  let snapshots = $state<BackendSnapshot[]>([]);
  /** Backends Panels updates, in the server's order, then any other it can sign in. */
  const backends = $derived.by(() => {
    const keys = (drivers.data?.drivers ?? [])
      .filter(driver => driver.checks_for_updates)
      .map(driver => driver.driver_kind);
    for (const snapshot of snapshots) {
      if (snapshot.can_sign_in && !keys.includes(snapshot.backend_key)) keys.push(snapshot.backend_key);
    }
    return keys.map(key => ({ key, label: backendLabel(key, drivers.data?.drivers) }));
  });
  let refreshing = $state(false);
  let refreshError = $state<string | null>(null);
  let updating = $state<Partial<Record<ConversationBackendKey, boolean>>>({});
  let results = $state<Partial<Record<ConversationBackendKey, BackendUpdateResult>>>({});
  const anyUpdating = $derived(Object.values(updating).some(Boolean));
  const AT_REST: SignInPageState = { signIn: null, busy: false, error: null };
  /** One sign-in session per backend that can be signed in. */
  const signInSessions = new Map<ConversationBackendKey, BackendSignInSession>();
  let signInStates = $state<Partial<Record<ConversationBackendKey, SignInPageState>>>({});

  async function refresh() {
    refreshing = true;
    refreshError = null;
    try {
      const result = await loadBackendPageSnapshots(value => { snapshots = value; },
        () => readBackends(false, machineId), () => readBackends(true, machineId));
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

  /** Entering finds a sign-in already in force on the server and resumes watching it. */
  function signInSession(key: ConversationBackendKey): BackendSignInSession {
    let session = signInSessions.get(key);
    if (session === undefined) {
      session = new BackendSignInSession({
        start: key => startBackendSignIn(key, machineId),
        read: key => readBackendSignIn(key, machineId),
        cancel: (key, sessionId) => cancelBackendSignIn(key, sessionId, machineId),
        submitReturnUrl: (key, sessionId, url) => submitBackendSignInReturnUrl(key, sessionId, url, machineId),
        reloadCards: () => refresh(),
        setTimer: (run, ms) => setTimeout(run, ms),
        clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)
      }, next => { signInStates[key] = next; });
      signInSessions.set(key, session);
      void session.enter(key);
    }
    return session;
  }

  $effect(() => {
    for (const snapshot of snapshots) {
      if (backendCanBeSignedIn(snapshot)) signInSession(snapshot.backend_key);
    }
  });

  onMount(() => {
    void refresh();
    // Leaving stops watching without cancelling a sign-in on the server.
    return () => { for (const session of signInSessions.values()) session.leave(); };
  });

  /** The Update button shows whenever Panels can run the update, as before the redesign:
   *  an update that is already current can still repair a broken installation. */
  function offersUpdate(snapshot: BackendSnapshot | undefined): boolean {
    return isHome && Boolean(snapshot?.installed && snapshot.update_advisory?.update_command);
  }

  function offersSignIn(snapshot: BackendSnapshot | undefined, signInState: SignInPageState): boolean {
    return snapshot !== undefined && backendCanBeSignedIn(snapshot)
      && snapshot.identity?.status === "unauthenticated" && !signInIsPending(signInState.signIn);
  }

  function failureLine(label: string, snapshot: BackendSnapshot | undefined): string {
    return snapshot?.version ? `The update did not finish. ${label} is still on ${snapshot.version}.` : "The update did not finish.";
  }
</script>

<SettingsPage title="Backends">
  {#snippet actions()}
    {@render machineChoice()}
    <Button variant="secondary" disabled={refreshing || anyUpdating} onclick={() => void refresh()}>
      {refreshing ? "Checking…" : isHome ? "Check for updates" : "Refresh"}
    </Button>
  {/snippet}
  {#if refreshError}<p class="backend-refresh-error" role="alert">Could not refresh backend versions. {refreshError}</p>{/if}
  {#if drivers.isPending && backends.length === 0}
    <span class="skeleton-status" role="status">Loading backends…</span>
    <div class="backend-skeleton" aria-hidden="true">
      <SettingsSection first>
        {#each Array.from({ length: 3 }, (_, index) => index) as index}
          <div class="backend-skeleton-row">
            <span class="skeleton-line" style:width={`${20 + ((index * 17) % 25)}%`}></span>
            <span class="skeleton-line backend-skeleton-description" style:width={`${40 + ((index * 23) % 30)}%`}></span>
          </div>
        {/each}
      </SettingsSection>
    </div>
  {:else}
    <SettingsSection first>
      {#each backends as backend (backend.key)}
        {@const snapshot = snapshots.find(item => item.backend_key === backend.key)}
        {@const result = results[backend.key]}
        {@const signInState = signInStates[backend.key] ?? AT_REST}
        {@const failed = result?.outcome === "failed" && !updating[backend.key]}
        <SettingsRow label={backend.label} description={backendStatus(snapshot, { refreshing, refreshError })} rowId={`backends:${backend.key}`}
          error={failed ? failureLine(backend.label, snapshot) : null}>
          {#snippet control()}
            <div class="backend-controls" data-backend={backend.key} aria-busy={!!updating[backend.key]}>
              {#if offersUpdate(snapshot) || updating[backend.key]}
                <Button disabled={!!updating[backend.key] || refreshing} onclick={() => void update(backend.key)} aria-label={`Update ${backend.label}`}>
                  {updating[backend.key] ? "Updating…" : "Update"}
                </Button>
              {/if}
              {#if offersSignIn(snapshot, signInState)}
                <Button variant="primary" disabled={signInState.busy} onclick={() => void signInSession(backend.key).start(backend.key)}
                  data-backend-sign-in-start={backend.key}>{signInState.busy ? "Starting…" : "Sign in"}</Button>
              {/if}
            </div>
          {/snippet}
          {#snippet after()}
            {#if result && !updating[backend.key]}
              <div class="backend-after">
                <p class="backend-result" role={result.outcome === "failed" ? "alert" : "status"}>{result.detail}</p>
                {#if result.outcome === "failed" && result.output_tail}
                  <details class="backend-output"><summary>Update output</summary><pre class="backend-codebox">{result.output_tail}</pre></details>
                {/if}
              </div>
            {/if}
            {#if snapshot}
              <BackendSignInBlock label={backend.label} {snapshot} state={signInState} offerStart={false}
                onStart={() => void signInSession(backend.key).start(backend.key)}
                onCancel={() => void signInSession(backend.key).cancel(backend.key)}
                onSubmit={returnUrl => void signInSession(backend.key).submitReturnUrl(backend.key, returnUrl)} />
            {/if}
          {/snippet}
        </SettingsRow>
      {/each}
    </SettingsSection>
  {/if}
</SettingsPage>

<style>
  .backend-refresh-error { margin:var(--page-block-gap) 0 0; font-size:var(--type-small); color:var(--accent-error); }
  .backend-controls { display:flex; align-items:center; gap:var(--space-2); flex:none; }
  .backend-after { display:grid; gap:var(--space-2); padding:0 0 var(--space-4); }
  .backend-result { margin:0; font-size:var(--type-small); line-height:1.45; color:var(--tone-tertiary); }
  .backend-output summary { width:max-content; cursor:pointer; font-size:var(--type-small); font-weight:var(--weight-small); color:var(--tone-tertiary); }
  .backend-output summary:hover { color:var(--tone-secondary); }
  .backend-codebox { margin:var(--space-2) 0 0; padding:var(--space-3); background:var(--surface-recessed); border-radius:var(--radius-md); font:var(--type-code)/1.6 var(--font-mono); color:var(--tone-primary); white-space:pre-wrap; overflow-wrap:anywhere; }
  .backend-skeleton { animation:backend-skeleton-in var(--skeleton-fade) ease-in-out var(--skeleton-delay) both; }
  .backend-skeleton-row { display:flex; flex-direction:column; justify-content:center; min-height:52px; }
  .backend-skeleton-description { height:8px; margin-top:var(--space-2); }
  @keyframes backend-skeleton-in { from { opacity:0; } to { opacity:1; } }
  @media (prefers-reduced-motion: reduce) { .backend-skeleton { animation:none; } }
</style>
