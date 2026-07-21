<script lang="ts">
  import type { ContentBlock } from "@agentclientprotocol/sdk";
  import type {
    ConversationSnapshot,
    DeepReadonly,
    TranscriptStanza,
  } from "../../lib/acp/conversationState";
  import { groupMessageBlocks } from "../../lib/acp/conversationState";
  import { acpFilePreviewTarget } from "../../lib/acp/filePreview";
  import type { ContextCompaction } from "../../lib/acp/contracts";
  import FilePreview from "../FilePreview.svelte";
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import StanzaView from "./StanzaView.svelte";

  let {
    timeline,
    session,
    compactions,
    protocolRejections,
    unsupportedAgentContent,
    terminalStates,
    programmaticPrompts,
    activity = null,
    onThoughtExpanded,
    onToolExpanded
  }: {
    timeline: ConversationSnapshot["timeline"];
    session: ConversationSnapshot["session"];
    compactions: ConversationSnapshot["compactions"];
    protocolRejections: ConversationSnapshot["protocolRejections"];
    unsupportedAgentContent: ConversationSnapshot["unsupportedAgentContent"];
    terminalStates: ConversationSnapshot["terminalStates"];
    programmaticPrompts: ConversationSnapshot["programmaticPrompts"];
    /** Delivery receipts no longer render in the transcript; accepted so the
     *  pane's existing invocation stays untouched. */
    receipts?: ConversationSnapshot["receipts"];
    activity?: ConversationSnapshot["activity"];
    onThoughtExpanded: (messageId: string, partIndex: number, expanded: boolean) => void;
    onToolExpanded: (toolCallId: string, expanded: boolean) => void;
  } = $props();

  let reasonExpanded = $state<Record<string, boolean>>({});

  type RenderItem =
    | { kind: "user"; key: string; messageId: string; content: readonly DeepReadonly<ContentBlock>[] }
    | { kind: "programmatic"; key: string; promptId: string; content: readonly DeepReadonly<ContentBlock>[]; source: "worker" | "role" }
    | { kind: "agent-content"; key: string; messageId: string; content: readonly DeepReadonly<ContentBlock>[] }
    | { kind: "stanza"; key: string; stanza: TranscriptStanza; live: boolean }
    | { kind: "compaction"; key: string; boundaryId: string }
    | { kind: "protocol"; key: string; sequence: number };

  const SYNTHETIC_LIVE_KEY = "stanza:live-synthetic";

  let items = $derived.by<RenderItem[]>(() => {
    const out: RenderItem[] = [];
    for (const reference of timeline) {
      if (reference.kind === "programmatic_prompt") {
        const prompt = programmaticPrompts[reference.promptId];
        if (prompt) {
          out.push({
            kind: "programmatic",
            key: `s:${reference.promptId}`,
            promptId: reference.promptId,
            content: prompt.payload.prompt.prompt,
            source: prompt.payload.source,
          });
        }
      } else if (reference.kind === "message") {
        const message = session.messages.find((item) => item.id === reference.messageId);
        if (!message) continue;
        if (message.role === "user") {
          for (const [partIndex, part] of message.parts.entries()) {
            if (part.type === "content") {
              out.push({ kind: "user", key: `u:${message.id}:p${partIndex}`, messageId: message.id, content: part.content });
            }
          }
          continue;
        }
        for (const block of groupMessageBlocks(message)) {
          if (block.kind === "content") {
            out.push({ kind: "agent-content", key: block.key, messageId: message.id, content: block.content });
          } else {
            out.push({ kind: "stanza", key: block.stanza.key, stanza: block.stanza, live: false });
          }
        }
      } else if (reference.kind === "compaction") {
        if (compactions[reference.compactionBoundaryId]) {
          out.push({ kind: "compaction", key: `c:${reference.compactionBoundaryId}`, boundaryId: reference.compactionBoundaryId });
        }
      } else if (reference.kind === "protocol_rejection") {
        if (protocolRejections[String(reference.protocolRejectionSequence)]) {
          out.push({ kind: "protocol", key: `p:${reference.protocolRejectionSequence}`, sequence: reference.protocolRejectionSequence });
        }
      }
      // delivery references leave the transcript entirely — their information lives in the queue tray.
    }

    // While the agent is thinking there is always exactly one live stanza. If
    // the tail is already a stanza it becomes the live one; otherwise (no agent
    // output yet, or the turn ended in prose) a bare live stanza is synthesized.
    if (activity?.state === "thinking" || activity?.state === "working") {
      const last = out[out.length - 1];
      if (last && last.kind === "stanza") {
        last.live = true;
      } else {
        out.push({
          kind: "stanza",
          key: SYNTHETIC_LIVE_KEY,
          live: true,
          stanza: {
            key: SYNTHETIC_LIVE_KEY,
            messageId: "",
            thoughtPartIndex: null,
            thought: null,
            thoughtExpanded: false,
            steps: [],
            hasFailure: false,
          },
        });
      }
    }
    return out;
  });

  function compactionText(payload: DeepReadonly<ContextCompaction>): string {
    if (payload.state === "compacting") return `context compacting · ${payload.trigger}`;
    if (payload.state === "compacted") return `context compacted · ${payload.trigger}`;
    return `context compaction failed · ${payload.reason ?? payload.trigger}`;
  }
</script>

{#snippet contentBlocks(content: readonly DeepReadonly<ContentBlock>[])}
  {#each content as block}
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
{/snippet}

<div class="acp-transcript" data-acp-transcript>
  {#each items as item (item.key)}
    {#if item.kind === "user"}
      <article class="chat-u" data-acp-message={item.messageId}>{@render contentBlocks(item.content)}</article>
    {:else if item.kind === "programmatic"}
      <article class="chat-system" data-acp-programmatic-prompt={item.promptId}>
        <div class="chat-system-label">System message · {item.source}</div>
        {@render contentBlocks(item.content)}
      </article>
    {:else if item.kind === "agent-content"}
      <article class="chat-a" data-acp-message={item.messageId}>{@render contentBlocks(item.content)}</article>
    {:else if item.kind === "stanza"}
      <StanzaView
        stanza={item.stanza}
        {terminalStates}
        live={item.live}
        {onThoughtExpanded}
        {onToolExpanded}
      />
    {:else if item.kind === "compaction"}
      {@const payload = compactions[item.boundaryId].payload}
      <div
        class="acp-compaction acp-compaction--{payload.state}"
        class:is-live={payload.state === "compacting"}
        data-acp-compaction={item.boundaryId}
        role="separator"
        aria-label={compactionText(payload)}
      >
        <span>{compactionText(payload)}</span>
      </div>
    {:else if item.kind === "protocol"}
      {@const rejection = protocolRejections[String(item.sequence)]}
      <div class="acp-pill-group" data-acp-protocol-rejection>
        <button
          type="button"
          class="acp-pill"
          aria-expanded={Boolean(reasonExpanded[item.key])}
          aria-controls={`acp-reason-${item.key}`}
          onclick={() => (reasonExpanded[item.key] = !reasonExpanded[item.key])}
        >unsupported update</button>
        {#if reasonExpanded[item.key]}
          <div id={`acp-reason-${item.key}`} class="acp-pill-reason">{rejection.reason}</div>
        {/if}
      </div>
    {/if}
  {/each}

  {#if activity?.state === "failed"}
    <div class="acp-turn-end acp-turn-end--error" role="alert">turn failed{activity.detail ? ` · ${activity.detail}` : ""}</div>
  {:else if activity?.state === "interrupted"}
    <div class="acp-turn-end acp-turn-end--warn">interrupted</div>
  {/if}

  {#each unsupportedAgentContent as item (item.key)}
    <div class="acp-pill-group" data-acp-unsupported>
      <button
        type="button"
        class="acp-pill"
        aria-expanded={Boolean(reasonExpanded[item.key])}
        aria-controls={`acp-reason-${item.key}`}
        onclick={() => (reasonExpanded[item.key] = !reasonExpanded[item.key])}
      >unsupported content</button>
      {#if reasonExpanded[item.key]}
        <div id={`acp-reason-${item.key}`} class="acp-pill-reason">{item.reason}</div>
      {/if}
    </div>
  {/each}
</div>
