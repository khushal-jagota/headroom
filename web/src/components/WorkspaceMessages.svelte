<script lang="ts">
  import type { WorkspaceMessageConversation } from "../lib/types";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import SprintTicketRow from "./SprintTicketRow.svelte";
  let { conversations, onHover = () => undefined, onLeave = () => undefined }: {
    conversations: WorkspaceMessageConversation[];
    onHover?: (target: HTMLElement, conversation: WorkspaceMessageConversation) => void;
    onLeave?: () => void;
  } = $props();
</script>
{#if conversations.length}
  <div class="bucket workspace-ticket-bucket" data-workspace-messages>
    <div class="bucket-label">Messages</div>
    {#each conversations as conversation (conversation.conversationId)}
      <SprintTicketRow priority={null} title={conversation.title} state="current-awaiting-approval" ariaLabel="Unread reply" class="worker-row"
        href={workspaceAddress(conversation.parentId && conversation.parentId !== "chief" ? {kind:"ticket",id:conversation.id,openedFromItemId:conversation.parentId} : {kind:"ticket",id:conversation.id})}
        data-message-conversation={conversation.conversationId} data-message-kind="ticket" data-workspace-mark="reply"
        onmouseenter={(event: MouseEvent) => onHover(event.currentTarget as HTMLElement, conversation)} onmouseleave={onLeave}
        onfocus={(event: FocusEvent) => onHover(event.currentTarget as HTMLElement, conversation)} onblur={onLeave}
        stageMarkAttributes={{"data-workspace-mark":"reply"}} />
    {/each}
  </div>
{/if}
