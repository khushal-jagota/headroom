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
  <div class="bucket" data-workspace-messages>
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
<style>
  .bucket { margin-top:var(--space-4); }
  .bucket-label { font-size:10.5px; line-height:normal; font-weight:600; letter-spacing:0.08em; text-transform:uppercase; color:var(--text-faint); padding-bottom:6px; margin-bottom:var(--space-1); border-bottom:var(--border-hairline) solid var(--border-color); }
  .bucket :global(.worker-row) { width:100%; display:grid; grid-template-columns:minmax(0,1fr) 14px; align-items:center; gap:var(--space-2); padding:var(--space-1) var(--space-2); margin:0 calc(var(--space-2) * -1); border:0; border-radius:var(--radius-sm); background:transparent; text-align:left; text-decoration:none; color:var(--text-muted); font:inherit; font-size:var(--type-sm); line-height:1.5; cursor:pointer; transition:color var(--motion-fast) var(--motion-ease),background var(--motion-fast) var(--motion-ease); }
  .bucket :global(.worker-row:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .bucket :global(.worker-row:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:1px; }
  .bucket :global(.ticket-row-title) { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .bucket :global(.worker-row .stage-mark) { justify-self:end; margin-left:0; margin-right:0; }
</style>
