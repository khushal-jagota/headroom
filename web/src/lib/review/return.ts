import type {NavigationReviewItem,ReviewHistoryKey,ViewRequest} from '../navigation/contracts';
import type {EffectiveNavigationView} from '../navigation/knowledge';

export function reviewHistoryKey(item:NavigationReviewItem):ReviewHistoryKey {
  return item.review_item_type==='ticket' ? {kind:'ticket',id:item.ticket_id} : {kind:'conversation',id:item.conversation_id};
}
export function sameReviewHistory(left:readonly ReviewHistoryKey[],right:readonly ReviewHistoryKey[]):boolean {
  const keys=new Set(left.map(key=>JSON.stringify([key.kind,key.id])));
  return keys.size===right.length && right.every(key=>keys.has(JSON.stringify([key.kind,key.id])));
}
export const SKIPPED_RETURN_LABEL='Back to skipped items';
/** A retained current publication can belong to the previous demand while a new scope loads. */
export function reviewReturnLabel(view:EffectiveNavigationView|null,request:ViewRequest,history:readonly ReviewHistoryKey[]):string {
  const summary=view?.publication.view?.reviewSummary;
  return view?.runtimeCurrent && view.publication.view?.scopeGeneration===request.scope_generation
    && sameReviewHistory(request.review_skipped_keys,history) && summary && summary.skipped>0 && summary.new===0
    ? SKIPPED_RETURN_LABEL : 'Back to review';
}
