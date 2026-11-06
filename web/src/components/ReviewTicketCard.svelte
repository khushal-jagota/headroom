<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { NavigationReviewTicketItem } from '../lib/types';
  import { navigationApplication } from '../lib/navigation/application.svelte';

  import { mutateJson } from '../lib/mutate';
  import { workspaceAddress } from '../lib/workspaceAddress';
  import { selectedRecap } from '../lib/navigation/views/recap.svelte';
  import RequestRecapAction from './RequestRecapAction.svelte';
  import Button from './Button.svelte';
  import InlineEdit from './InlineEdit.svelte';
  import ClampedText from './ClampedText.svelte';
  import MarkdownBlock from './MarkdownBlock.svelte';
  let {item,onSkip,busy=false,inlineDecision=false,children}:{item:NavigationReviewTicketItem;onSkip:()=>void;busy?:boolean;inlineDecision?:boolean;children:Snippet}=$props();
  const navigation=navigationApplication()!;
  let record=$derived(navigation.selectors.effective ? navigation.presentationFor(navigation.selectors.effective).rawTicketsById.get(item.ticket_id)?.value : undefined);
  const recapBody=selectedRecap(()=>record && 'recap_reference' in record ? record.recap_reference as import('../lib/navigation/contracts').ViewContentReference|null : null,()=>true);
  let recap=$derived(recapBody.body ?? record?.recap ?? '');
  async function saveTitle(title:string):Promise<unknown> {
    return mutateJson(`/api/tickets/${item.ticket_id}`,{method:'PATCH',body:{title}});
  }
</script>
<div data-review-ticket-card data-review-item-type="ticket" data-ticket-id={item.ticket_id}>
  {#if inlineDecision}
    <!-- Skip and Open ticket sit in the decision card's own action row; this is the page's breadcrumb slot. -->
    <div class="review-eyebrow page-eyebrow">Review</div>
  {:else}
  <div class="review-ticket-decision-line">
    <Button variant="ghost" data-skip disabled={busy} onclick={onSkip}>Skip</Button>
    <Button variant="ghost" data-open-ticket href={workspaceAddress({kind:'ticket',id:item.ticket_id})}>Open ticket</Button>
  </div>
  {/if}
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
