<script lang="ts">
  /** What one backend on this machine is, and whether it needs anything from you.
   *
   * Everything on this card is either something the machine said or an honest absence.
   * A backend that is not signed in names the terminal command that signs it in and
   * stops there, because signing in is out of band everywhere. An update button appears
   * only when there is an update Panels can actually run, and what it reports afterwards
   * is which of three things happened — including the one that changed nothing.
   */
  import Button from "../Button.svelte";
  import type {
    BackendSnapshot,
    BackendUpdateResult,
    BackendUsageResult
  } from "../../lib/conversation/wire";

  let {
    snapshot,
    updating = false,
    result = null,
    onUpdate,
    usageRefreshing = false,
    usageResult = null,
    usageError = null,
    onUsageRefresh
  }: {
    snapshot: BackendSnapshot;
    updating?: boolean;
    result?: BackendUpdateResult | null;
    onUpdate?: () => void;
    usageRefreshing?: boolean;
    usageResult?: BackendUsageResult | null;
    usageError?: string | null;
    onUsageRefresh?: () => void;
  } = $props();

  let identityLine = $derived.by(() => {
    const identity = snapshot.identity;
    if (identity === null) return "no account to sign in to";
    if (identity.status === "authenticated") {
      return [identity.account_label ?? "signed in", identity.detail]
        .filter((part) => part)
        .join(" · ");
    }
    if (identity.status === "unauthenticated") {
      return identity.login_command
        ? `not signed in · run ${identity.login_command} in a terminal`
        : "not signed in";
    }
    return "Panels could not read who this is signed in as";
  });

  let advisory = $derived(snapshot.update_advisory);

  function formatPercent(value: number): string {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)}% used`;
  }

  function formatReset(value: string): string {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(new Date(value));
  }
</script>

<article class="c2-backend" data-conversation-backend={snapshot.backend_key}>
  <header class="c2-backend-head">
    <span class="c2-backend-name">{snapshot.backend_key}</span>
    <span class="c2-backend-version">
      {#if !snapshot.installed}
        not installed
      {:else}
        {snapshot.version ?? "version unknown"}
      {/if}
    </span>
  </header>

  {#if snapshot.installed}
    <dl class="c2-backend-facts">
      <dt>signed in</dt>
      <dd data-conversation-backend-identity>{identityLine}</dd>
      <dt>models</dt>
      <dd data-conversation-backend-models>
        {#if snapshot.available_models.length === 0}
          none offered here
        {:else}
          {#each snapshot.available_models as model (model.model_id)}
            <span class="c2-backend-model">
              {model.display_name ?? model.model_id}
              {#if model.detail}
                <span class="c2-backend-model-detail" data-conversation-backend-model-detail>
                  {model.detail}
                </span>
              {/if}
            </span>
          {/each}
        {/if}
      </dd>
      <dt>reasoning effort</dt>
      <dd data-conversation-backend-efforts>
        {snapshot.reasoning_effort_options.length > 0
          ? snapshot.reasoning_effort_options.join(", ")
          : "this backend has no such setting"}
      </dd>
    </dl>
  {/if}

  {#if advisory}
    <div class="c2-backend-advisory">
      <span data-conversation-backend-advisory>{advisory.detail}</span>
      {#if advisory.update_command && onUpdate}
        <Button
          variant="quiet"
          disabled={updating}
          onclick={onUpdate}
          data-conversation-backend-update={snapshot.backend_key}
        >{updating ? "Updating…" : "Update"}</Button>
      {/if}
    </div>
  {/if}

  {#if snapshot.installed && snapshot.backend_key !== "hermes" && onUsageRefresh}
    <section class="c2-backend-usage" aria-label="Usage limits">
      <div class="c2-backend-usage-head">
        <span>usage limits</span>
        <Button
          variant="quiet"
          disabled={usageRefreshing}
          onclick={onUsageRefresh}
          data-conversation-backend-usage-refresh={snapshot.backend_key}
        >{usageRefreshing ? "Refreshing…" : usageResult ? "Refresh" : "Check usage"}</Button>
      </div>

      {#if usageError}
        <p class="c2-backend-usage-note is-failed" role="alert">{usageError}</p>
      {:else if usageResult?.outcome === "succeeded"}
        <div class="c2-backend-usage-windows" data-conversation-backend-usage="succeeded">
          {#each usageResult.windows as window (window.name)}
            <div class="c2-backend-usage-window">
              <span>{window.name}</span>
              <strong>{formatPercent(window.used_percent)}</strong>
              <small>resets {formatReset(window.resets_at)}</small>
            </div>
          {/each}
        </div>
        {#if usageResult.observed_at}
          <p class="c2-backend-usage-observed">
            checked {formatReset(usageResult.observed_at)}
          </p>
        {/if}
      {:else if usageResult}
        <p
          class="c2-backend-usage-note"
          class:is-failed={usageResult.outcome === "failed"}
          data-conversation-backend-usage={usageResult.outcome}
        >{usageResult.detail ?? "Usage limits are not available."}</p>
      {:else}
        <p class="c2-backend-usage-note">Checked only when you press the button.</p>
      {/if}
    </section>
  {/if}

  {#if result}
    <p
      class="c2-backend-result"
      class:is-failed={result.outcome === "failed"}
      data-conversation-backend-result={result.outcome}
    >{result.outcome} · {result.detail}</p>
    {#if result.output_tail}
      <pre class="c2-backend-output">{result.output_tail}</pre>
    {/if}
  {/if}

  {#each snapshot.diagnoses as diagnosis (diagnosis)}
    <p class="c2-backend-diagnosis" data-conversation-backend-diagnosis>{diagnosis}</p>
  {/each}
</article>

<style>
  .c2-backend {
    display: grid;
    gap: var(--space-2);
    min-width: 0;
    padding: var(--space-3);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-md);
    background: var(--surface-raised);
  }
  .c2-backend-head { display: flex; align-items: baseline; gap: var(--space-2); }
  .c2-backend-name {
    color: var(--text-strong);
    font-family: var(--font-mono);
    font-size: var(--type-sm);
    letter-spacing: var(--tracking-mono);
  }
  .c2-backend-version {
    margin-inline-start: auto;
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  .c2-backend-facts {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--space-1) var(--space-3);
    margin: 0;
    color: var(--text-muted);
    font-size: var(--type-xs);
  }
  .c2-backend-facts dt { color: var(--text-faintest); font-family: var(--font-mono); }
  .c2-backend-facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  .c2-backend-model { display: block; }
  /* What the name reaches, when the name alone does not say. */
  .c2-backend-model-detail {
    display: block;
    color: var(--text-faintest);
    font-family: var(--font-mono);
  }
  .c2-backend-advisory {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-faint);
    font-size: var(--type-xs);
  }
  .c2-backend-advisory span { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .c2-backend-result {
    margin: 0;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
  .c2-backend-result.is-failed { color: var(--accent-error); }
  .c2-backend-output {
    margin: 0;
    max-height: var(--file-preview-markdown-max-height);
    overflow: auto;
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
  }
  .c2-backend-usage {
    display: grid;
    gap: var(--space-2);
    padding-top: var(--space-2);
    border-top: var(--border-hairline) solid var(--border-color);
  }
  .c2-backend-usage-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }
  .c2-backend-usage-windows { display: grid; gap: var(--space-2); }
  .c2-backend-usage-window {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--space-1) var(--space-3);
    color: var(--text-muted);
    font-size: var(--type-xs);
  }
  .c2-backend-usage-window strong {
    color: var(--text-default);
    font-family: var(--font-mono);
    font-weight: 500;
  }
  .c2-backend-usage-window small {
    grid-column: 1 / -1;
    color: var(--text-faintest);
    font-family: var(--font-mono);
  }
  .c2-backend-usage-note,
  .c2-backend-usage-observed {
    margin: 0;
    color: var(--text-faint);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
  .c2-backend-usage-observed {
    color: var(--text-faintest);
    font-family: var(--font-mono);
  }
  .c2-backend-usage-note.is-failed { color: var(--accent-error); }
  .c2-backend-diagnosis {
    margin: 0;
    color: var(--text-faint);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
</style>
