<script lang="ts">
  import type { WorkspaceMessageConversation } from "../lib/types";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import StageMark from "./StageMark.svelte";
  let { conversations, open = $bindable(true), heading = $bindable(null) }: {
    conversations: WorkspaceMessageConversation[]; open?: boolean; heading?: HTMLElement | null;
  } = $props();
</script>
<details class="workspace-messages" bind:open data-workspace-messages>
  <summary bind:this={heading}><span>Messages</span><span class="rule"></span><span class="count" data-messages-count>{conversations.length}</span></summary>
  {#each conversations as conversation (conversation.conversationId)}
    <a class="message-row" href={workspaceAddress(conversation.kind === "ticket" ? (conversation.parentId && conversation.parentId !== "chief" ? {kind:"ticket",id:conversation.id,openedFromItemId:conversation.parentId} : {kind:"ticket",id:conversation.id}) : conversation.kind === "chief" ? {kind:"chief"} : {kind:"item",id:conversation.id})} data-message-conversation={conversation.conversationId} data-message-kind={conversation.kind}>
      <span class="message-identity"><span class="message-title">{conversation.title}</span><span class="message-context">{conversation.kind === "ticket" ? conversation.parentTitle ?? "Chief of Staff" : conversation.kind === "chief" ? "Never settles" : "Orchestrator"}{conversation.settled ? " · settled" : ""}</span></span>
      <StageMark state="current-awaiting-approval" aria-label="Unread reply" data-workspace-mark="reply" />
    </a>
  {/each}
</details>
<style>
  .workspace-messages { margin-top:var(--space-5); }
  summary { display:flex; align-items:center; gap:var(--space-3); padding:var(--space-2) 0; font:inherit; color:var(--text-faint); cursor:pointer; list-style:none; }
  summary::-webkit-details-marker { display:none; }
  .rule { flex:1; height:var(--border-hairline); background:var(--border-color); }
  .count { flex:none; font-size:var(--type-xs); color:var(--text-faintest); font-variant-numeric:tabular-nums; }
  .message-row { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-1) var(--space-2); margin:0 calc(var(--space-2) * -1); border-radius:var(--radius-sm); color:var(--text-muted); text-decoration:none; }
  .message-row:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .message-identity { flex:1; min-width:0; }
  .message-title { display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:var(--type-sm); }
  .message-context { display:block; color:var(--text-faintest); font-size:var(--type-xs); }
  summary:focus-visible,.message-row:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
</style>
