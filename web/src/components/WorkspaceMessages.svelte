<script lang="ts">
  import { onDestroy } from "svelte";
  import { createRailPreviewInput } from "../lib/workspaceRailInteraction";
  import type { WorkspaceMessageConversation } from "../lib/types";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import SprintTicketRow from "./SprintTicketRow.svelte";
  let { conversations, motionPrefix = undefined, onHover = () => undefined, onLeave = () => undefined }: {
    conversations: WorkspaceMessageConversation[];
    motionPrefix?: string;
    onHover?: (target: HTMLElement, conversation: WorkspaceMessageConversation, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  const inputs = new Map<string, ReturnType<typeof createRailPreviewInput>>();
  function input(conversation: WorkspaceMessageConversation) {
    let controller = inputs.get(conversation.conversationId);
    if (!controller) {
      controller = createRailPreviewInput((target, immediate) => {
        const fresh = conversations.find((row) => row.conversationId === conversation.conversationId);
        if (fresh) onHover(target, fresh, immediate);
      }, onLeave);
      inputs.set(conversation.conversationId, controller);
    }
    return controller;
  }
  $effect(() => {
    const present = new Set(conversations.map((row) => row.conversationId));
    for (const [key, controller] of inputs) if (!present.has(key)) { controller.dispose(); inputs.delete(key); }
  });
  onDestroy(() => { for (const controller of inputs.values()) controller.dispose(); });
</script>
{#if conversations.length}
  <div class="bucket workspace-ticket-bucket" data-workspace-messages>
    <div class="bucket-label" data-rail-motion={motionPrefix ? `settled-heading:${motionPrefix}` : undefined}>Messages</div>
    {#each conversations as conversation (conversation.conversationId)}
      <SprintTicketRow title={conversation.title} signal={conversation.conversationSignal} conversationId={conversation.conversationId} class="worker-row"
        href={workspaceAddress(conversation.kind === "chief" ? {kind:"chief"} : conversation.kind === "sprint_item" ? {kind:"item",id:conversation.id} : conversation.parentId && conversation.parentId !== "chief" ? {kind:"ticket",id:conversation.id,openedFromItemId:conversation.parentId} : {kind:"ticket",id:conversation.id})}
        data-rail-motion={motionPrefix ? `settled-message:${motionPrefix}:${conversation.conversationId}` : undefined} data-message-conversation={conversation.conversationId} data-message-kind={conversation.kind}
        onpointerenter={(event: PointerEvent) => input(conversation).pointerEnter(event)} onpointerleave={onLeave}
        onpointerdown={(event: PointerEvent) => input(conversation).down(event)} onpointermove={(event: PointerEvent) => input(conversation).move(event)}
        onpointerup={() => input(conversation).end()} onpointercancel={() => input(conversation).cancel()}
        onfocus={(event: FocusEvent) => input(conversation).focus(event)} onblur={onLeave}
        onclick={(event: MouseEvent) => { input(conversation).click(event); }} />
    {/each}
  </div>
{/if}
