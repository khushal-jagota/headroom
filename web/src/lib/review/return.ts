import type {NavigationReviewItem,ReviewHistoryKey,ViewRequest,ReviewSummary} from '../navigation/contracts';
import type {EffectiveNavigationView} from '../navigation/knowledge';

export function reviewHistoryKey(item:NavigationReviewItem):ReviewHistoryKey {
  return item.review_item_type==='ticket' ? {kind:'ticket',id:item.ticket_id} : {kind:'conversation',id:item.conversation_id};
}
export function sameReviewHistory(left:readonly ReviewHistoryKey[],right:readonly ReviewHistoryKey[]):boolean {
  const keys=new Set(left.map(key=>JSON.stringify([key.kind,key.id])));
  return keys.size===right.length && right.every(key=>keys.has(JSON.stringify([key.kind,key.id])));
}
export const NEW_RETURN_LABEL='Review new';
export const SKIPPED_RETURN_LABEL='Review skipped';
/** What the final page knows of the queue. Pending is a view that is one request behind and on its way. Unavailable is a queue that cannot load. */
export type ReviewSummaryState={kind:'known';summary:ReviewSummary}|{kind:'pending'}|{kind:'unavailable'};
/** A retained current publication can belong to the previous demand while a new scope loads. */
export function currentReviewSummary(view:EffectiveNavigationView|null,request:ViewRequest,history:readonly ReviewHistoryKey[],answeredTotal=0,readFailed=false):ReviewSummaryState {
  const publication=view?.publication.view;
  if(!view?.runtimeCurrent || !publication)return {kind:'unavailable'};
  // The global total does not depend on the demanded conversation context or skip history.
  // It can prove the queue is clear while a replacement scope catches up after a reply.
  if(Math.max(0,publication.counts.review-answeredTotal)===0)return {kind:'known',summary:{new:0,skipped:0}};
  // A replacement view that the owner is retrying after a failed read is not coming soon: the queue cannot load.
  if(publication.scopeGeneration!==request.scope_generation || !sameReviewHistory(request.review_skipped_keys,history))return readFailed ? {kind:'unavailable'} : {kind:'pending'};
  return publication.reviewSummary ? {kind:'known',summary:publication.reviewSummary} : {kind:'unavailable'};
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
