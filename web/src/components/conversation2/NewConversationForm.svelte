<script lang="ts">
  /** The empty state: a conversation that has been named but not yet started.
   *
   * Everything on it is still changeable, because none of it has been committed to
   * anything — the conversation is created by the first message, not by opening this.
   * The backend picker only exists here for the same reason: once a conversation has a
   * backend it has a live process behind it, and that is not a dropdown any more.
   */
  import { CONVERSATION_BACKEND_KEYS } from "../../lib/conversation2/wire";
  import type { BackendSnapshot, ConversationBackendKey } from "../../lib/conversation2/wire";

  let {
    conversationId,
    backends = [],
    backendKey = $bindable("codex" as ConversationBackendKey),
    model = $bindable<string | null>(null),
    reasoningEffort = $bindable<string | null>(null),
    workspaceFolder = $bindable("~/Coding")
  }: {
    conversationId: string;
    backends?: readonly BackendSnapshot[];
    backendKey?: ConversationBackendKey;
    model?: string | null;
    reasoningEffort?: string | null;
    workspaceFolder?: string;
  } = $props();

  let chosen = $derived(backends.find((snapshot) => snapshot.backend_key === backendKey) ?? null);
  let models = $derived(chosen?.available_models ?? []);
  let effortOptions = $derived(chosen?.reasoning_effort_options ?? []);

  function chooseBackend(key: ConversationBackendKey): void {
    if (key === backendKey) return;
    backendKey = key;
    // The old choices belonged to the old backend and mean nothing to this one.
    model = null;
    reasoningEffort = null;
  }
</script>

<section class="c2-new" data-conversation2-new aria-label="New conversation">
  <h2 class="c2-new-title">New conversation</h2>
  <p class="c2-new-lede">
    Nothing has started yet. Choose what this runs as, then send the first message — that
    is what creates it.
  </p>

  <div class="c2-new-field">
    <span class="c2-new-label">backend</span>
    <div class="chat-seg" role="group" aria-label="Backend">
      {#each CONVERSATION_BACKEND_KEYS as key (key)}
        <button
          type="button"
          class:on={backendKey === key}
          aria-pressed={backendKey === key}
          data-conversation2-new-backend={key}
          onclick={() => chooseBackend(key)}
        >{key}</button>
      {/each}
    </div>
  </div>

  <label class="c2-new-field">
    <span class="c2-new-label">model</span>
    <select
      data-conversation2-new-model
      value={model ?? ""}
      onchange={(event) => (model = event.currentTarget.value || null)}
    >
      <option value="">the backend's own model</option>
      {#each models as candidate (candidate.model_id)}
        <option value={candidate.model_id}>{candidate.display_name ?? candidate.model_id}</option>
      {/each}
    </select>
  </label>

  {#if effortOptions.length > 0}
    <label class="c2-new-field">
      <span class="c2-new-label">reasoning effort</span>
      <select
        data-conversation2-new-effort
        value={reasoningEffort ?? ""}
        onchange={(event) => (reasoningEffort = event.currentTarget.value || null)}
      >
        <option value="">the backend's own effort</option>
        {#each effortOptions as effort (effort)}
          <option value={effort}>{effort}</option>
        {/each}
      </select>
    </label>
  {/if}

  <label class="c2-new-field">
    <span class="c2-new-label">workspace folder</span>
    <input data-conversation2-new-workspace type="text" bind:value={workspaceFolder} />
  </label>

  {#if chosen && !chosen.installed}
    <p class="c2-new-warning" data-conversation2-new-warning>
      {chosen.diagnoses[0] ?? `${backendKey} is not installed.`}
    </p>
  {/if}

  <p class="c2-new-id" data-conversation2-new-id>{conversationId}</p>
</section>

<style>
  .c2-new {
    align-self: center;
    display: grid;
    gap: var(--space-3);
    min-width: 0;
    width: 100%;
    max-width: var(--measure-read);
    padding: var(--space-4);
    border: var(--border-hairline) dashed var(--border-color);
    border-radius: var(--radius-lg);
    background: var(--surface-raised);
  }
  .c2-new-title {
    margin: 0;
    color: var(--text-strong);
    font-family: var(--font-serif);
    font-size: var(--type-serif-lg);
    letter-spacing: var(--tracking-title);
  }
  .c2-new-lede {
    margin: 0;
    color: var(--text-muted);
    font-family: var(--font-serif);
    font-size: var(--type-serif-sm);
    line-height: 1.6;
  }
  .c2-new-field { display: flex; align-items: center; gap: var(--space-3); min-width: 0; }
  .c2-new-label {
    flex: none;
    width: var(--space-page-tail);
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
  }
  .c2-new-field select,
  .c2-new-field input {
    flex: 1;
    min-width: 0;
    background: var(--surface-sunken);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--text-default);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding: var(--space-2);
  }
  .c2-new-warning { margin: 0; color: var(--accent-error); font-size: var(--type-xs); }
  .c2-new-id {
    margin: 0;
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
</style>
