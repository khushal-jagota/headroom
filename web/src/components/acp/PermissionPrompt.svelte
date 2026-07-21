<script lang="ts">
  import { mount, unmount } from "svelte";
  import type { PermissionViewState } from "../../lib/acp/conversationState";
  import DiffView from "./DiffView.svelte";

  let {
    permission,
    onSelect
  }: {
    permission: PermissionViewState;
    onSelect: (requestId: string, optionId: string) => void;
  } = $props();

  let titleId = $derived(`acp-permission-title-${permission.request.requestId}`);
  let statusId = $derived(`acp-permission-status-${permission.request.requestId}`);
  let diffs = $derived(
    (permission.request.request.toolCall.content ?? []).filter((content) => content.type === "diff")
  );
  let permissionElement = $state<HTMLElement>();
  let optionsElement = $state<HTMLDivElement>();

  $effect(() => {
    const target = permissionElement;
    const anchor = optionsElement;
    if (!target || !anchor || diffs.length === 0) return;
    const mountedDiffs = diffs.map((diff) => mount(DiffView, {
      target,
      anchor,
      props: { path: diff.path, oldText: diff.oldText, newText: diff.newText }
    }));
    return () => {
      for (const mountedDiff of mountedDiffs) void unmount(mountedDiff);
    };
  });
</script>

<section
  bind:this={permissionElement}
  class="acp-permission"
  aria-labelledby={titleId}
  aria-describedby={statusId}
  data-acp-permission={permission.request.requestId}
>
  <div id={titleId} class="acp-permission-title">
    {permission.request.request.toolCall.title ?? "Agent permission request"}
  </div>
  <div bind:this={optionsElement} class="acp-permission-options" role="group" aria-label="Permission options">
    {#each permission.request.request.options as option (option.optionId)}
      <button
        type="button"
        data-permission-kind={option.kind}
        disabled={permission.submittingOptionId !== null}
        aria-pressed={permission.submittingOptionId === option.optionId}
        onclick={() => onSelect(permission.request.requestId, option.optionId)}
      >
        {permission.submittingOptionId === option.optionId ? `${option.name}…` : option.name}
      </button>
    {/each}
  </div>
  <div id={statusId} class="acp-permission-status" aria-live="polite">
    {permission.submittingOptionId ? "Sending permission response" : "Waiting for your decision"}
  </div>
</section>

<style>
  .acp-permission {
    background: var(--accent-surface);
    border: var(--border-hairline) solid var(--accent-bright);
    border-radius: var(--radius-md);
    color: var(--accent-text);
    display: grid;
    gap: var(--space-3);
    padding: var(--space-3);
  }
  .acp-permission-title { font-family: var(--font-ui); font-size: var(--type-sm); }
  .acp-permission-options { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  button {
    background: transparent;
    border: var(--border-hairline) solid var(--accent-bright);
    border-radius: var(--radius-sm);
    color: var(--accent-text);
    cursor: pointer;
    font: inherit;
    padding: var(--space-2) var(--space-3);
  }
  button:disabled { cursor: default; opacity: 0.7; }
  .acp-permission-status { font-family: var(--font-mono); font-size: var(--type-xs); }
</style>
