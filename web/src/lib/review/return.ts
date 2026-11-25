import type {NavigationReviewItem,ReviewHistoryKey,ViewRequest,ReviewSummary} from '../navigation/contracts';
import type {EffectiveNavigationView} from '../navigation/knowledge';

export function reviewHistoryKey(item:NavigationReviewItem):ReviewHistoryKey {
  return item.review_item_type==='ticket' ? {kind:'ticket',id:item.ticket_id} : {kind:'conversation',id:item.conversation_id};
}
export function sameReviewHistory(left:readonly ReviewHistoryKey[],right:readonly ReviewHistoryKey[]):boolean {
  const keys=new Set(left.map(key=>JSON.stringify([key.kind,key.id])));
  return keys.size===right.length && right.every(key=>keys.has(JSON.stringify([key.kind,key.id])));
}
export const SKIPPED_RETURN_LABEL='Review skipped';
/** A retained current publication can belong to the previous demand while a new scope loads. */
export function currentReviewSummary(view:EffectiveNavigationView|null,request:ViewRequest,history:readonly ReviewHistoryKey[]):ReviewSummary|null {
  const summary=view?.publication.view?.reviewSummary;
  return view?.runtimeCurrent && view.publication.view?.scopeGeneration===request.scope_generation
    && sameReviewHistory(request.review_skipped_keys,history) && summary ? summary : null;
}

/** Remove locally completed cards before the publication catches up. */
export function remainingReviewSummary(summary:ReviewSummary|null,answeredNew:number,answeredSkipped:number):ReviewSummary|null {
  return summary ? {new:Math.max(0,summary.new-answeredNew),skipped:Math.max(0,summary.skipped-answeredSkipped)} : null;
}

export function reviewFinalStatus(summary:ReviewSummary|null):readonly [string,string] {
  if(!summary)return ['Unavailable.','Review can’t load right now.'];
  if(summary.new && summary.skipped)return [`${summary.new+summary.skipped} to review.`,`${summary.new} new, ${summary.skipped} skipped.`];
  if(summary.skipped)return [`${summary.skipped} skipped.`,'Nothing new.'];
  if(summary.new)return [`${summary.new} new.`,'Ready to review.'];
  return ['All clear.','Nothing to review.'];
}
