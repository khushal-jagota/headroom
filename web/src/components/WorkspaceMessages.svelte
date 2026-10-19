<script lang="ts">
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { viewWindow } from "../lib/navigation/views/owner";
  import { navigationWindow } from "../lib/navigation/presentation";
  import { navigationScrollWindow } from "../lib/navigation/views/scrollWindow";
  import { onDestroy } from "svelte";
  import { createRailPreviewInput } from "../lib/workspaceRailInteraction";
  import type { WorkspaceMessageConversation } from "../lib/types";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import SprintTicketRow from "./SprintTicketRow.svelte";
  let { parentId=undefined, conversations, motionPrefix = undefined, onHover = () => undefined, onLeave = () => undefined }: {
    parentId?:string;
    conversations: WorkspaceMessageConversation[];
    motionPrefix?: string;
    onHover?: (target: HTMLElement, conversation: WorkspaceMessageConversation, immediate?: boolean) => void;
    onLeave?: () => void;
  } = $props();
  const navigation=navigationApplication();
  const demand=`messages:${crypto.randomUUID()}`;
  let visible=$state(false);
  let anchor=$state<string|null>(null);
  let direction=$state<'before'|'after'>('after');
  let scopedWindow=$derived(parentId?navigationWindow(navigation?.state.effective && navigation ? navigation.presentationFor(navigation.state.effective):undefined,'unread',{kind:'orchestrator',id:parentId}):undefined);
  let scopedConversations=$derived(scopedWindow?conversations.filter(row=>scopedWindow.members.some(key=>navigation?.state.publication?.records.some(record=>record.kind==='address' && record.id===key.id && record.value.conversation_id===row.conversationId))):conversations);
  $effect(()=>{if(parentId&&visible)navigation?.owner.setDemand(demand,{windows:[viewWindow('unread',{kind:'orchestrator',id:parentId},{limit:60,anchor,direction})]});else navigation?.owner.setDemand(demand,undefined);});
  onDestroy(()=>navigation?.owner.setDemand(demand,undefined));
  function visibility(node:HTMLElement){const observer=new IntersectionObserver(entries=>visible=entries[0].isIntersecting,{root:node.closest('[data-settled-scroll]'),rootMargin:'200px'});observer.observe(node);return{destroy(){observer.disconnect();}};}
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
<div use:visibility use:navigationScrollWindow={{window:scopedWindow,activate:()=>navigation?.owner.prioritizeWindow(scopedWindow?.request),selector:'[data-message-conversation]',key:node=>node.dataset.messageConversation ?? '',move:(next,nextDirection)=>{anchor=next;direction=nextDirection;}}}>
{#if scopedConversations.length || (scopedWindow?.total ?? 0)>0}
  <div class="bucket workspace-ticket-bucket" data-workspace-messages>
    <div class="bucket-label" data-rail-motion={motionPrefix ? `settled-heading:${motionPrefix}` : undefined}>Messages{#if scopedWindow} <span class="count">({scopedWindow.total})</span>{/if}</div>
    {#each scopedConversations as conversation (conversation.conversationId)}
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

</div>
