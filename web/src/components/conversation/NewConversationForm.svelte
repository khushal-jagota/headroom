<script lang="ts">
  /** The empty state: a conversation that has been named but not yet started.
   *
   * Everything on it is still changeable, because none of it has been committed to
   * anything — the conversation is created by the first message, not by opening this.
   * The backend picker only exists here for the same reason: once a conversation has a
   * backend it has a live process behind it, and that is not a dropdown any more.
   */
  import { resolveModelPicker } from "../../lib/conversation/modelPicker";
  import type { BackendSnapshot, ConversationBackendKey } from "../../lib/conversation/wire";
  import UnifiedModelPicker from "./UnifiedModelPicker.svelte";

  let {
    conversationId,
    backends = [],
    backendKey = $bindable("codex" as ConversationBackendKey),
    model = $bindable<string | null>(null),
    reasoningEffort = $bindable<string | null>(null),
    workspaceFolder = $bindable("~/projects")
  }: {
    conversationId: string;
    backends?: readonly BackendSnapshot[];
    backendKey?: ConversationBackendKey;
    model?: string | null;
    reasoningEffort?: string | null;
    workspaceFolder?: string;
  } = $props();

  let chosen = $derived(backends.find((snapshot) => snapshot.backend_key === backendKey) ?? null);
  let picker = $derived(resolveModelPicker({
    backendKey,
    model,
    reasoningEffort,
    backends
  }));

  function focusMessageBox(): void {
    document.querySelector<HTMLElement>("[data-conversation-input]")?.focus();
  }
</script>

<section class="c2-new" data-conversation-new aria-label="New conversation">
  <h2 class="c2-new-title">New conversation</h2>
  <p class="c2-new-lede">
    Nothing has started yet. Choose what this runs as, then send the first message — that
    is what creates it.
  </p>

  <div class="c2-new-field">
    <UnifiedModelPicker
      view={picker}
      snapshots={backends}
      models={chosen?.available_models ?? []}
      backendEffortOptions={chosen?.reasoning_effort_options ?? []}
      below
      attributes={{ "data-conversation-new-model-picker": "" }}
      afterChoose={focusMessageBox}
      onChooseBackend={(backend, defaults) => {
        backendKey = backend;
        model = defaults.model;
        reasoningEffort = defaults.reasoningEffort;
      }}
      onChooseModel={(nextModel, nextReasoningEffort) => {
        model = nextModel;
        reasoningEffort = nextReasoningEffort;
      }}
      onChooseReasoningEffort={(nextReasoningEffort) => {
        model = model ?? picker.defaultModel;
        reasoningEffort = nextReasoningEffort;
      }}
    />
  </div>

  <label class="c2-new-field">
    <input
      data-conversation-new-workspace
      type="text"
      aria-label="Workspace folder"
      bind:value={workspaceFolder}
    />
  </label>

  {#if chosen && !chosen.installed}
    <p class="c2-new-warning" data-conversation-new-warning>
      {chosen.diagnoses[0] ?? `${backendKey} is not installed.`}
    </p>
  {/if}

  <p class="c2-new-id" data-conversation-new-id>{conversationId}</p>
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
    background: var(--surface-2);
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
  .c2-new-field { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
  .c2-new-field input {
    flex: 1;
    min-width: 0;
    max-width: 100%;
    background: var(--surface-recessed);
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
