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
  let toolCall = $derived(permission.request.request.toolCall);
  let diffs = $derived((toolCall.content ?? []).filter((content) => content.type === "diff"));
  let permissionElement = $state<HTMLElement>();
  let optionsElement = $state<HTMLDivElement>();

  let options = $derived(permission.request.request.options);
  let rejectOptions = $derived(options.filter((option) => option.kind.startsWith("reject")));
  let allowOptions = $derived(options.filter((option) => !option.kind.startsWith("reject")));
  // The filled primary is the last option whose kind is an ACP allow kind; invented kinds
  // stay quiet outline on the right.
  let primaryOptionId = $derived(
    [...allowOptions].reverse().find((option) => option.kind.startsWith("allow"))?.optionId
  );

  // Countdown to the server-owned deadline; ticks per second, clamps at 0:00, never cancels.
  let now = $state(Date.now());
  let countdown = $derived.by(() => {
    if (!permission.request.deadlineAt) return null;
    const remaining = Math.max(0, Math.floor((permission.request.deadlineAt - now) / 1000));
    return `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  });

  $effect(() => {
    if (!permission.request.deadlineAt) return;
    const interval = setInterval(() => {
      now = Date.now();
    }, 1000);
    return () => clearInterval(interval);
  });

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
  data-acp-permission={permission.request.requestId}
>
  <div class="acp-permission-head">
    {#if toolCall.kind}
      <span class="acp-permission-kind">{toolCall.kind}</span>
    {/if}
    <span id={titleId} class="acp-permission-title">
      {toolCall.title ?? "Agent permission request"}
    </span>
    {#if countdown}
      <span class="acp-permission-count">{countdown}</span>
    {/if}
  </div>
  <div bind:this={optionsElement} class="acp-permission-options" role="group" aria-label="Permission options">
    {#each rejectOptions as option (option.optionId)}
      <button
        type="button"
        class="acp-permission-reject"
        data-permission-kind={option.kind}
        disabled={permission.submittingOptionId !== null}
        aria-pressed={permission.submittingOptionId === option.optionId}
        onclick={() => onSelect(permission.request.requestId, option.optionId)}
      >
        {option.name}
      </button>
    {/each}
    {#each allowOptions as option, index (option.optionId)}
      <button
        type="button"
        class="acp-permission-allow"
        class:primary={option.optionId === primaryOptionId}
        class:gather={index === 0}
        data-permission-kind={option.kind}
        disabled={permission.submittingOptionId !== null}
        aria-pressed={permission.submittingOptionId === option.optionId}
        onclick={() => onSelect(permission.request.requestId, option.optionId)}
      >
        {option.name}
      </button>
    {/each}
  </div>
</section>

<style>
  .acp-permission {
    background: var(--accent-surface);
    border-radius: var(--radius-md);
    color: var(--accent-text);
    display: grid;
    gap: var(--space-3);
    padding: var(--space-3);
  }
  .acp-permission-head { display: flex; align-items: baseline; gap: var(--space-2); }
  .acp-permission-kind {
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
    color: var(--accent-bright);
  }
  .acp-permission-title { font-family: var(--font-ui); font-size: var(--type-sm); color: var(--accent-text); }
  .acp-permission-count {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    color: var(--accent-bright);
    font-variant-numeric: tabular-nums;
  }
  .acp-permission-options { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
  .acp-permission-options .acp-permission-allow.gather { margin-left: auto; }
  button { font: inherit; cursor: pointer; }
  .acp-permission-reject {
    background: transparent;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--text-faint);
    padding: var(--space-2) 0;
  }
  .acp-permission-reject:hover,
  .acp-permission-reject:focus-visible { color: var(--accent-error); }
  .acp-permission-allow {
    background: transparent;
    border: var(--border-hairline) solid rgba(154, 173, 210, 0.45);
    border-radius: var(--radius-sm);
    color: var(--accent-text);
    padding: var(--space-2) var(--space-3);
  }
  .acp-permission-allow:hover,
  .acp-permission-allow:focus-visible { border-color: var(--accent-bright); }
  .acp-permission-allow.primary {
    background: var(--accent-bright);
    border-color: var(--accent-bright);
    color: var(--accent-ink);
    font-weight: 600;
    padding: var(--space-2) var(--space-4);
  }
  .acp-permission-allow.primary:hover { filter: brightness(1.08); }
  button:disabled { cursor: default; opacity: 0.7; }
</style>
