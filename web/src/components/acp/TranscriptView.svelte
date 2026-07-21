<script lang="ts">
  import type { ConversationSnapshot } from "../../lib/acp/conversationState";
  import { acpFilePreviewTarget } from "../../lib/acp/filePreview";
  import FilePreview from "../FilePreview.svelte";
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import PlanView from "./PlanView.svelte";
  import ThoughtView from "./ThoughtView.svelte";
  import ToolCallCard from "./ToolCallCard.svelte";

  let {
    timeline,
    session,
    compactions,
    receipts,
    protocolRejections,
    unsupportedAgentContent,
    terminalStates,
    onThoughtExpanded,
    onToolExpanded
  }: {
    timeline: ConversationSnapshot["timeline"];
    session: ConversationSnapshot["session"];
    compactions: ConversationSnapshot["compactions"];
    receipts: ConversationSnapshot["receipts"];
    protocolRejections: ConversationSnapshot["protocolRejections"];
    unsupportedAgentContent: ConversationSnapshot["unsupportedAgentContent"];
    terminalStates: ConversationSnapshot["terminalStates"];
    onThoughtExpanded: (messageId: string, partIndex: number, expanded: boolean) => void;
    onToolExpanded: (toolCallId: string, expanded: boolean) => void;
  } = $props();

  let rejectionExpanded = $state<Record<string, boolean>>({});
</script>

<div class="acp-transcript" data-acp-transcript>
  {#each timeline as reference}
    {#if reference.kind === "message"}
      {@const message = session.messages.find((item) => item.id === reference.messageId)}
      {#if message}
        <article class={message.role === "user" ? "chat-u" : "chat-a"} data-acp-message={message.id}>
          {#each message.parts as part, partIndex}
            {#if part.type === "content"}
              {#each part.content as block}
                {#if block.type === "text"}
                  <MarkdownBlock text={block.text} />
                {:else if block.type === "resource_link"}
                  {@const target = acpFilePreviewTarget(block.uri, block.title ?? block.name)}
                  {#if target}<FilePreview {target} mode="embedded" />{:else}<div class="acp-unsupported">Unsupported agent content</div>{/if}
                {:else if block.type === "resource" && "text" in block.resource}
                  <MarkdownBlock text={block.resource.text} />
                {:else if block.type === "image"}
                  <img src={`data:${block.mimeType};base64,${block.data}`} alt="Conversation content" />
                {:else if block.type === "audio"}
                  <audio controls src={`data:${block.mimeType};base64,${block.data}`}></audio>
                {:else}
                  <div class="acp-unsupported">Unsupported agent content</div>
                {/if}
              {/each}
            {:else if part.type === "thought"}
              <ThoughtView
                {part}
                messageId={message.id}
                {partIndex}
                onExpanded={(expanded) => onThoughtExpanded(message.id, partIndex, expanded)}
              />
            {:else if part.type === "tool_calls"}
              {#each part.toolCalls as tool (tool.toolCallId)}
                <ToolCallCard
                  {tool}
                  {terminalStates}
                  onExpanded={(expanded) => onToolExpanded(tool.toolCallId, expanded)}
                />
              {/each}
            {:else if part.type === "plan"}
              <PlanView entries={part.plan} />
            {/if}
          {/each}
        </article>
      {/if}
    {:else if reference.kind === "compaction"}
      {@const compaction = compactions[reference.compactionBoundaryId]}
      {#if compaction}
        <div class="acp-inline" data-acp-compaction={reference.compactionBoundaryId}>
          Context {compaction.payload.state} · {compaction.payload.trigger}{compaction.payload.reason ? ` · ${compaction.payload.reason}` : ""}
        </div>
      {/if}
    {:else if reference.kind === "delivery"}
      {@const receipt = receipts[reference.deliveryClientMessageId]}
      {#if receipt}
        <div class="acp-inline" data-acp-delivery={reference.deliveryClientMessageId}>
          {receipt.state}{receipt.queuePosition ? ` · queue ${receipt.queuePosition}` : ""}{receipt.reason ? ` · ${receipt.reason}` : ""}
        </div>
      {/if}
    {:else if reference.kind === "protocol_rejection"}
      {@const rejection = protocolRejections[String(reference.protocolRejectionSequence)]}
      {#if rejection}
        <section class="acp-inline acp-protocol" data-acp-protocol-rejection>
          <button
            type="button"
            aria-expanded={Boolean(rejectionExpanded[String(rejection.sequence)])}
            aria-controls={`acp-protocol-${rejection.sequence}`}
            onclick={() => (rejectionExpanded[String(rejection.sequence)] = !rejectionExpanded[String(rejection.sequence)])}
          >{rejection.status}</button>
          {#if rejectionExpanded[String(rejection.sequence)]}
            <div id={`acp-protocol-${rejection.sequence}`}>{rejection.reason}</div>
          {/if}
        </section>
      {/if}
    {/if}
  {/each}

  {#each unsupportedAgentContent as item (item.key)}
    <div class="acp-inline acp-unsupported" data-acp-unsupported>{item.reason}</div>
  {/each}
</div>

<style>
  .acp-transcript { display: contents; }
  article { display: grid; gap: var(--space-3); }
  img { display: block; max-width: 100%; }
  audio { max-width: 100%; }
  .acp-inline {
    border-left: var(--border-hairline) solid var(--border-color);
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding-left: var(--space-3);
  }
  .acp-inline button {
    background: transparent;
    border: 0;
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    padding: var(--space-1) 0;
  }
  .acp-inline button:hover { color: var(--text-strong); }
  .acp-protocol { border-left-color: var(--accent-error); }
  .acp-unsupported { color: var(--text-faint); }
</style>
