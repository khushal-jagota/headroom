<script lang="ts">
  import { getContext, type Snippet } from 'svelte';
  import { DECK_CONTEXT, deckGapOut, type Deck } from '../lib/review/deckMotion';
  import type { NavigationReviewTicketItem } from '../lib/types';
  import { navigationApplication } from '../lib/navigation/application.svelte';

  import { mutateJson } from '../lib/mutate';
  import { workspaceAddress } from '../lib/workspaceAddress';
  import { selectedRecap } from '../lib/navigation/views/recap.svelte';
  import RequestRecapAction from './RequestRecapAction.svelte';
  import Button from './Button.svelte';
  import ReviewPassButton from './ReviewPassButton.svelte';
  import WindowPageRow from './desktopWindow/WindowPageRow.svelte';
  import InlineEdit from './InlineEdit.svelte';
  import ClampedText from './ClampedText.svelte';
  import MarkdownBlock from './MarkdownBlock.svelte';
  let {item,onSkip,busy=false,inlineDecision=false,children}:{item:NavigationReviewTicketItem;onSkip:()=>void;busy?:boolean;inlineDecision?:boolean;children:Snippet}=$props();
  const navigation=navigationApplication()!;
  const deck=getContext<Deck>(DECK_CONTEXT);
  let record=$derived(navigation.selectors.effective ? navigation.presentationFor(navigation.selectors.effective).rawTicketsById.get(item.ticket_id)?.value : undefined);
  const recapBody=selectedRecap(()=>record && 'recap_reference' in record ? record.recap_reference as import('../lib/navigation/contracts').ViewContentReference|null : null,()=>true);
  let recap=$derived(recapBody.body ?? record?.recap ?? '');
  async function saveTitle(title:string):Promise<unknown> {
    return mutateJson(`/api/tickets/${item.ticket_id}`,{method:'PATCH',body:{title}});
  }
</script>
<div data-review-ticket-card data-review-item-type="ticket" data-ticket-id={item.ticket_id}>
  {#if inlineDecision}
    <!-- Skip and Open ticket stand in the page's top band at the right, as staging (owner's
         choice); the band keeps every page's height, so the title starts on every page's line. -->
    <!-- In the desktop window the front card's row is pinned on the window's top row. -->
    <WindowPageRow>
    <div class="review-eyebrow review-eyebrow--decisions">
      <div class="review-ticket-decision-line review-ticket-decision-line--top" data-review-top-decisions data-window-row-actions>
        <ReviewPassButton />
        <Button variant="ghost" data-open-ticket href={workspaceAddress({kind:'ticket',id:item.ticket_id})}>Open ticket</Button>
        <Button variant="ghost" data-skip disabled={busy} onclick={onSkip}>Skip</Button>
      </div>
    </div>
    </WindowPageRow>
  {:else}
  <WindowPageRow>
  <div class="review-ticket-decision-line" data-window-row-actions>
    <ReviewPassButton />
    <Button variant="ghost" data-open-ticket href={workspaceAddress({kind:'ticket',id:item.ticket_id})}>Open ticket</Button>
    <Button variant="ghost" data-skip disabled={busy} onclick={onSkip}>Skip</Button>
  </div>
  </WindowPageRow>
  {/if}
  <div class="review-ticket-heading"><div class="review-ticket-heading-main">
    <div class="review-ticket-title"><InlineEdit value={item.title} placeholder="Untitled" onSave={saveTitle}/></div>
    {#if recap}<ClampedText class="review-recap" contentKey={recap}><MarkdownBlock text={recap}/></ClampedText>{/if}
    {#if item.messages.length}<div class="review-recap-request" data-deck-part out:deckGapOut={{deck}}><RequestRecapAction kind="ticket" id={item.ticket_id}/></div>{/if}
  </div></div>
  {@render children()}
</div>

<style>
  .review-recap-request{display:flex;justify-content:flex-end;margin-top:var(--space-2);font-size:var(--type-sm)}
  .review-eyebrow--decisions{justify-content:flex-end}
  /* On a desktop the quiet buttons' words line up with the column's edge; on a phone the column
     has no room past its edge, so they stay inside it. */
  .review-ticket-decision-line--top{padding:0;margin-right:calc(var(--space-3) * -1)}
  @media (max-width:720px){.review-ticket-decision-line--top{margin-right:0}}
</style>
