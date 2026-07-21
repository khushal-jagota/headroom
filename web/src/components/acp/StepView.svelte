<script lang="ts">
  import type { ToolCallState } from "../../vendor/acp-components-core/src/types/index";
  import type { ConversationTerminalState } from "../../lib/acp/contracts";
  import type { DeepReadonly } from "../../lib/acp/conversationState";
  import { acpFilePreviewTarget } from "../../lib/acp/filePreview";
  import { lineDiff } from "../../lib/acp/lineDiff";
  import { stepIconPaths } from "../../lib/acp/stepIcons";
  import FilePreview from "../FilePreview.svelte";
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import DiffView from "./DiffView.svelte";

  let {
    tool,
    terminalStates,
    onExpanded
  }: {
    tool: DeepReadonly<ToolCallState>;
    terminalStates: Readonly<Record<string, DeepReadonly<ConversationTerminalState>>>;
    onExpanded: (expanded: boolean) => void;
  } = $props();

  let regionId = $derived(`acp-step-${tool.toolCallId}`);
  let iconPaths = $derived(stepIconPaths(tool.kind));
  let detail = $derived(
    (tool.content ?? []).filter(
      (content) => content.type === "diff" || content.type === "terminal" || content.type === "content",
    ),
  );
  let expandable = $derived(detail.length > 0);
  let open = $derived(Boolean(tool.expanded));

  // A failed step arrives with its detail open — once, so it can still be
  // collapsed afterwards.
  let failureOpened = $state(false);
  $effect(() => {
    if (tool.status !== "failed" || failureOpened) return;
    failureOpened = true;
    if (!tool.expanded) onExpanded(true);
  });

  let counts = $derived.by(() => {
    if (tool.kind !== "edit") return null;
    let added = 0;
    let removed = 0;
    let sawDiff = false;
    for (const content of tool.content ?? []) {
      if (content.type !== "diff") continue;
      sawDiff = true;
      for (const row of lineDiff(content.oldText, content.newText)) {
        if (row.kind === "add") added += 1;
        else if (row.kind === "delete") removed += 1;
      }
    }
    return sawDiff ? { added, removed } : null;
  });

  function terminalLabel(terminal: DeepReadonly<ConversationTerminalState>): string {
    if (terminal.lifecycle === "released") return "released";
    const exit = terminal.terminalOutput.exitStatus;
    if (!exit) return "running";
    if (exit.signal) return `signal ${exit.signal}`;
    return `exited ${exit.exitCode ?? "unknown"}`;
  }
</script>

{#snippet mark()}
  {#if tool.status === "completed"}
    <span class="acp-step-mark acp-mark-ok" role="img" aria-label="Completed">✓</span>
  {:else if tool.status === "failed"}
    <span class="acp-step-mark acp-mark-fail" role="img" aria-label="Failed">✕</span>
  {:else if tool.status === "in_progress"}
    <span class="acp-spin" role="img" aria-label="In progress"></span>
  {:else}
    <span class="acp-step-mark acp-mark-pend" role="img" aria-label="Pending">○</span>
  {/if}
{/snippet}

{#snippet body()}
  <span class="acp-step-icon" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      {#each iconPaths as path}<path d={path} />{/each}
    </svg>
  </span>
  <span class="acp-step-title">{tool.title}</span>
  {#if counts}
    <span class="acp-step-counts">
      <span class="acp-step-add">+{counts.added}</span> <span class="acp-step-del">−{counts.removed}</span>
    </span>
  {/if}
  {@render mark()}
{/snippet}

{#if expandable}
  <button
    type="button"
    class="acp-step acp-step--expandable"
    data-acp-step={tool.toolCallId}
    aria-expanded={open}
    aria-controls={regionId}
    onclick={() => onExpanded(!Boolean(tool.expanded))}
  >{@render body()}</button>
  {#if open}
    <div id={regionId} class="acp-step-detail">
      {#each detail as content}
        {#if content.type === "diff"}
          <DiffView path={content.path} oldText={content.oldText} newText={content.newText} />
        {:else if content.type === "terminal"}
          {@const terminal = terminalStates[content.terminalId]}
          <section class="acp-code-well" aria-label={`Terminal ${content.terminalId}`}>
            <div class="acp-code-well-label">
              terminal {content.terminalId} · {terminal ? terminalLabel(terminal) : "waiting for output"}
            </div>
            {#if terminal}
              <pre>{terminal.terminalOutput.output}</pre>
              {#if terminal.terminalOutput.truncated}<div>Earlier output was truncated</div>{/if}
            {/if}
          </section>
        {:else if content.type === "content"}
          {@const block = content.content}
          {#if block.type === "text"}
            <MarkdownBlock text={block.text} />
          {:else if block.type === "resource_link"}
            {@const target = acpFilePreviewTarget(block.uri, block.title ?? block.name)}
            {#if target}<FilePreview {target} mode="embedded" />{:else}<div class="acp-unsupported">Unsupported agent content</div>{/if}
          {:else if block.type === "resource" && "text" in block.resource}
            <MarkdownBlock text={block.resource.text} />
          {:else if block.type === "image"}
            <img src={`data:${block.mimeType};base64,${block.data}`} alt="Agent-provided content" />
          {:else if block.type === "audio"}
            <audio controls src={`data:${block.mimeType};base64,${block.data}`}></audio>
          {:else}
            <div class="acp-unsupported">Unsupported agent content</div>
          {/if}
        {/if}
      {/each}
    </div>
  {/if}
{:else}
  <div class="acp-step" data-acp-step={tool.toolCallId}>{@render body()}</div>
{/if}
