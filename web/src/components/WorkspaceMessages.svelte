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
      <SprintTicketRow locale={conversation.locale} ticketId={conversation.kind === "ticket" ? conversation.id : undefined} priority={null} title={conversation.title} signal={conversation.conversationSignal} conversationId={conversation.conversationId} class="worker-row"
        href={workspaceAddress(conversation.kind === "chief" ? {kind:"chief"} : conversation.kind === "sprint_item" ? {kind:"item",id:conversation.id} : conversation.parentId && conversation.parentId !== "chief" ? {kind:"ticket",id:conversation.id,openedFromItemId:conversation.parentId} : {kind:"ticket",id:conversation.id})}
        data-message-conversation={conversation.conversationId} data-message-kind={conversation.kind}
        onmouseenter={(event: MouseEvent) => onHover(event.currentTarget as HTMLElement, conversation)} onmouseleave={onLeave}
        onfocus={(event: FocusEvent) => onHover(event.currentTarget as HTMLElement, conversation)} onblur={onLeave} />
    {/each}
  </div>
{/if}
