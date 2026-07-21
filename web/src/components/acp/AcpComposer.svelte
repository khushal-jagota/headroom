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
    onCancelQueued
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
  } = $props();

  let deliveryChoice = $state<TurnDeliveryChoice>("queue");
  let composerError = $state<string | null>(null);
  let active = $derived(["thinking", "working", "compacting", "waiting_for_permission"].includes(activityState ?? ""));
  let latestReceipt = $derived(
    latestReceiptClientMessageId ? receipts[latestReceiptClientMessageId] ?? null : null
  );
  // Only failures speak below the box; rejected deliveries are the sole receipt state shown.
  let rejectedReceipt = $derived(
    latestReceipt && latestReceipt.state === "rejected"
      ? `rejected${latestReceipt.reason ? ` · ${latestReceipt.reason}` : ""}`
      : null
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
  <div class="chat-box-stack">
    {#if queue.length}
      <ol class="chat-queue-tray" aria-label="Queued prompts">
        {#each queue as item, index (item.clientMessageId)}
          <li class="chat-qrow">
            <span class="chat-qrow-n">{index + 1}</span>
            <span class="chat-qrow-txt">{promptLabel(item.prompt)}</span>
            <button
              type="button"
              class="chat-qrow-x"
              aria-label="Cancel queued prompt"
              onclick={() => onCancelQueued(item.clientMessageId)}
            >×</button>
          </li>
        {/each}
      </ol>
    {/if}

    <ConversationComposer
      {commands}
      placeholder={`Message ${employeeLabel}...`}
      {active}
      {supportsSteer}
      {deliveryChoice}
      onDeliveryChoice={(choice) => (deliveryChoice = choice)}
      onStop={onCancelActive}
      onSubmit={submit}
      onError={setComposerError}
    />
  </div>

  {#if composerError}
    <div class="chat-receipt" role="alert" data-acp-composer-error>{composerError}</div>
  {:else if rejectedReceipt}
    <div class="chat-receipt" role="alert">{rejectedReceipt}</div>
  {/if}
</section>

<style>
  .acp-composer { display: grid; gap: var(--space-2); }
</style>
