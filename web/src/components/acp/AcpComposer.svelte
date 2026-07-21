<script lang="ts">
  import type { AvailableCommand, ContentBlock } from "@agentclientprotocol/sdk";
  import type {
    ConversationActivityState,
    QueuedPrompt,
    TurnDeliveryChoice,
    TurnDeliveryReceipt
  } from "../../lib/acp/contracts";
  import type { ConversationActionResult } from "../../lib/acp/conversationController";
  import type { DeepReadonly } from "../../lib/acp/conversationState";
  import ConversationComposer from "./ConversationComposer.svelte";

  let {
    commands,
    queue,
    receipts,
    latestReceiptClientMessageId,
    activityState,
    supportsSteer,
    employeeLabel,
    onPrompt,
    onCancelActive,
    onCancelQueued,
    onNewConversation
  }: {
    commands: readonly DeepReadonly<AvailableCommand>[];
    queue: readonly DeepReadonly<QueuedPrompt>[];
    receipts: Readonly<Record<string, DeepReadonly<TurnDeliveryReceipt>>>;
    latestReceiptClientMessageId: string | null;
    activityState: ConversationActivityState | null;
    supportsSteer: boolean;
    employeeLabel: string;
    onPrompt: (contentBlocks: ContentBlock[], choice: TurnDeliveryChoice) => ConversationActionResult;
    onCancelActive: () => void;
    onCancelQueued: (clientMessageId: string) => void;
    onNewConversation: () => void;
  } = $props();

  let deliveryChoice = $state<TurnDeliveryChoice>("queue");
  let composerError = $state<string | null>(null);
  let active = $derived(["thinking", "working", "compacting", "waiting_for_permission"].includes(activityState ?? ""));
  let latestReceipt = $derived(
    latestReceiptClientMessageId ? receipts[latestReceiptClientMessageId] ?? null : null
  );
  async function imageBlock(file: File): Promise<ContentBlock> {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return { type: "image", data: btoa(binary), mimeType: file.type };
  }

  async function submit(text: string, _mode: "message" | "command", files: File[] = []): Promise<boolean> {
    const content: ContentBlock[] = [];
    if (text.trim()) content.push({ type: "text", text });
    for (const file of files) content.push(await imageBlock(file));
    const result = onPrompt(content, active ? deliveryChoice : "normal");
    if (!result.ok) throw new Error(result.reason ?? "Conversation action could not be sent");
    return true;
  }

  function setComposerError(error: unknown | null): void {
    composerError = error instanceof Error ? error.message : error === null ? null : String(error);
  }

  function promptLabel(prompt: DeepReadonly<QueuedPrompt["prompt"]>): string {
    return prompt.prompt.map((block) => {
      if (block.type === "text") return block.text;
      if (block.type === "image") return "Image";
      if (block.type === "audio") return "Audio";
      return "Resource";
    }).join(" · ");
  }
</script>

<section class="acp-composer" data-acp-composer>
  {#if queue.length}
    <ol class="acp-queue" aria-label="Queued prompts">
      {#each queue as item (item.clientMessageId)}
        <li>
          <span>{promptLabel(item.prompt)}</span>
          <button type="button" onclick={() => onCancelQueued(item.clientMessageId)}>Cancel</button>
        </li>
      {/each}
    </ol>
  {/if}

  {#if active}
    <div class="acp-delivery" role="group" aria-label="Delivery choice">
      <button
        type="button"
        aria-pressed={deliveryChoice === "steer"}
        disabled={!supportsSteer}
        title={supportsSteer ? "Steer the active turn" : "Steer is unavailable for this employee"}
        onclick={() => (deliveryChoice = "steer")}
      >Steer</button>
      <button
        type="button"
        aria-pressed={deliveryChoice === "send_now"}
        onclick={() => (deliveryChoice = "send_now")}
      >Send Now</button>
      <button
        type="button"
        aria-pressed={deliveryChoice === "queue"}
        onclick={() => (deliveryChoice = "queue")}
      >Queue</button>
    </div>
    {#if !supportsSteer}<div class="acp-steer-help">Steer is unavailable for this employee.</div>{/if}
  {/if}

  <ConversationComposer
    {commands}
    placeholder={`Message ${employeeLabel}...`}
    onSubmit={submit}
    onError={setComposerError}
  />

  <div class="acp-lifecycle">
    {#if active}<button type="button" onclick={onCancelActive}>Stop</button>{/if}
    <button type="button" onclick={onNewConversation}>New conversation</button>
  </div>

  <div class="acp-receipt" aria-live="polite">
    {#if composerError}
      <span data-acp-composer-error>{composerError}</span>
    {:else if latestReceipt}
      {latestReceipt.state}{latestReceipt.queuePosition ? ` · queue ${latestReceipt.queuePosition}` : ""}{latestReceipt.reason ? ` · ${latestReceipt.reason}` : ""}
    {/if}
  </div>
</section>

<style>
  .acp-composer { display: grid; gap: var(--space-2); }
  .acp-queue { display: grid; gap: var(--space-1); list-style: none; margin: 0; padding: 0; }
  .acp-queue li {
    align-items: center;
    color: var(--text-muted);
    display: flex;
    font-size: var(--type-xs);
    gap: var(--space-2);
    justify-content: space-between;
  }
  .acp-delivery, .acp-lifecycle { display: flex; flex-wrap: wrap; gap: var(--space-1); }
  button {
    background: transparent;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    padding: var(--space-1) var(--space-2);
  }
  button[aria-pressed="true"] { border-color: var(--accent-bright); color: var(--text-strong); }
  button:disabled { cursor: default; color: var(--text-faintest); }
  .acp-steer-help, .acp-receipt {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>
