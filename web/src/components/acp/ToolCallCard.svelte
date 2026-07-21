<script lang="ts">
  import type { ToolCallState } from "../../vendor/acp-components-core/src/types/index";
  import type {
    ConversationTerminalState,
  } from "../../lib/acp/contracts";
  import type { DeepReadonly } from "../../lib/acp/conversationState";
  import { safeDisplayText } from "../../lib/acp/conversationState";
  import { acpFilePreviewTarget } from "../../lib/acp/filePreview";
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

  let rawExpanded = $state(false);
  let regionId = $derived(`acp-tool-${tool.toolCallId}`);
  let rawRegionId = $derived(`acp-tool-raw-${tool.toolCallId}`);
  let hasRaw = $derived(tool.rawInput !== undefined || tool.rawOutput !== undefined);

  function terminalLabel(terminal: DeepReadonly<ConversationTerminalState>): string {
    if (terminal.lifecycle === "released") return "released";
    const exit = terminal.terminalOutput.exitStatus;
    if (!exit) return "running";
    if (exit.signal) return `signal ${exit.signal}`;
    return `exited ${exit.exitCode ?? "unknown"}`;
  }
</script>

<section class="acp-tool" data-acp-tool={tool.toolCallId}>
  <button
    type="button"
    class="acp-tool-toggle"
    aria-expanded={Boolean(tool.expanded)}
    aria-controls={regionId}
    onclick={() => onExpanded(!tool.expanded)}
  >
    <span>{tool.title}</span>
    <span class="acp-tool-meta">{tool.kind ?? "tool"} · {tool.status ?? "pending"}</span>
  </button>
  {#if tool.expanded}
    <div id={regionId} class="acp-tool-body">
      {#each tool.content ?? [] as content}
        {#if content.type === "diff"}
          <DiffView path={content.path} oldText={content.oldText} newText={content.newText} />
        {:else if content.type === "terminal"}
          {@const terminal = terminalStates[content.terminalId]}
          <section class="acp-terminal" aria-label={`Terminal ${content.terminalId}`}>
            <div class="acp-terminal-label">
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
            {#if target}<FilePreview {target} mode="embedded" />{:else}<div>Unsupported agent content</div>{/if}
          {:else if block.type === "resource" && "text" in block.resource}
            <MarkdownBlock text={block.resource.text} />
          {:else if block.type === "image"}
            <img src={`data:${block.mimeType};base64,${block.data}`} alt="Agent-provided content" />
          {:else if block.type === "audio"}
            <audio controls src={`data:${block.mimeType};base64,${block.data}`}></audio>
          {:else}
            <div class="acp-unsupported">Unsupported agent content</div>
          {/if}
        {:else}
          <div class="acp-unsupported">Unsupported agent content</div>
        {/if}
      {/each}

      {#if hasRaw}
        <button
          type="button"
          class="acp-raw-toggle"
          aria-expanded={rawExpanded}
          aria-controls={rawRegionId}
          onclick={() => (rawExpanded = !rawExpanded)}
        >
          Raw input and output
        </button>
        {#if rawExpanded}
          <div id={rawRegionId} class="acp-raw">
            {#if tool.rawInput !== undefined}<pre>{safeDisplayText(tool.rawInput)}</pre>{/if}
            {#if tool.rawOutput !== undefined}<pre>{safeDisplayText(tool.rawOutput)}</pre>{/if}
          </div>
        {/if}
      {/if}
    </div>
  {/if}
</section>

<style>
  .acp-tool {
    border-left: var(--border-hairline) solid var(--border-color);
    padding-left: var(--space-3);
  }
  .acp-tool-toggle, .acp-raw-toggle {
    align-items: baseline;
    background: transparent;
    border: 0;
    color: var(--text-muted);
    cursor: pointer;
    display: flex;
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    gap: var(--space-2);
    padding: var(--space-1) 0;
    text-align: left;
  }
  .acp-tool-toggle:hover, .acp-raw-toggle:hover { color: var(--text-strong); }
  .acp-tool-meta, .acp-terminal-label {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  .acp-tool-body { display: grid; gap: var(--space-3); margin-top: var(--space-2); }
  .acp-terminal, .acp-raw {
    background: var(--surface-sunken);
    color: var(--text-default);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding: var(--space-2);
  }
  pre { margin: 0; overflow-x: auto; white-space: pre-wrap; }
  img { display: block; max-width: 100%; }
  audio { max-width: 100%; }
  .acp-unsupported { color: var(--text-faint); }
</style>
