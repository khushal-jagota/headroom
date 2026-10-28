<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { NavigationReviewTicketItem } from '../lib/types';
  import { navigationApplication } from '../lib/navigation/application.svelte';
  import { ticketCommandImpact } from '../lib/ticketNavigationImpact';
  import { mutateJson } from '../lib/mutate';
  import { workspaceAddress } from '../lib/workspaceAddress';
  import { selectedRecap } from '../lib/navigation/views/recap.svelte';
  import RequestRecapAction from './RequestRecapAction.svelte';
  import InlineEdit from './InlineEdit.svelte';
  import ClampedText from './ClampedText.svelte';
  import MarkdownBlock from './MarkdownBlock.svelte';
  let {item,onSkip,busy=false,children}:{item:NavigationReviewTicketItem;onSkip:()=>void;busy?:boolean;children:Snippet}=$props();
  const navigation=navigationApplication()!;
  let record=$derived(navigation.selectors.effective ? navigation.presentationFor(navigation.selectors.effective).rawTicketsById.get(item.ticket_id)?.value : undefined);
  const recapBody=selectedRecap(()=>record && 'recap_reference' in record ? record.recap_reference as import('../lib/navigation/contracts').ViewContentReference|null : null,()=>true);
  let recap=$derived(recapBody.body ?? record?.recap ?? '');
  async function saveTitle(title:string):Promise<unknown> {
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,item.ticket_id,'title'),responseMetadata=>
      mutateJson(`/api/tickets/${item.ticket_id}`,{method:'PATCH',responseMetadata,body:{title}}),'review ticket title');
  }
</script>
<div data-review-ticket-card data-review-item-type="ticket" data-ticket-id={item.ticket_id}>
  <div class="review-ticket-decision-line">
    <button data-skip disabled={busy} onclick={onSkip}>Skip &rsaquo;</button>
    <a data-open-ticket href={workspaceAddress({kind:'ticket',id:item.ticket_id})}>Open ticket &rsaquo;</a>
  </div>
  <div class="review-ticket-heading"><div class="review-ticket-heading-main">
    <div class="review-ticket-title"><InlineEdit value={item.title} placeholder="Untitled" onSave={saveTitle}/></div>
    {#if recap}<ClampedText class="review-recap" contentKey={recap}><MarkdownBlock text={recap}/></ClampedText>{/if}
    {#if item.messages.length}<div class="review-recap-request"><RequestRecapAction kind="ticket" id={item.ticket_id}/></div>{/if}
  </div></div>
  {@render children()}
</div>

<style>
  .review-recap-request{margin-top:var(--space-2);font-size:var(--type-sm)}
</style>
