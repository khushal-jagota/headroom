import type { RecordImpact } from './navigation/commandEvidence';
import type { NavigationPresentation } from './navigation/presentation';
import { recordImpactTouches as touches } from './navigation/commandEvidence';
export function reviewBadgeImpact(impact:RecordImpact):boolean {
  return impact.kind==='ticket' && touches(impact,['ticket_status','ceiling_holder','gating_field','proposal_field','proposal_created_at']);
}
export function reviewQueueImpact(impact:RecordImpact,presentation:NavigationPresentation | undefined):boolean {
  return reviewBadgeImpact(impact) || impact.kind==='ticket' && Boolean(presentation?.review.items.some(item=>item.ticket_id===impact.id)) && touches(impact,['title','proposal_revision']);
}
export function reviewProposalImpact(impact:RecordImpact,id:string):boolean {
  return impact.kind==='ticket' && impact.id===id && touches(impact,['title','stage','ceiling','ceiling_holder','gating_field','proposal_field','proposal_created_at','proposal_revision']);
}
export function runtimeCountImpact(impact:RecordImpact,presentation:NavigationPresentation | undefined):boolean {
  if(impact.kind==='ticket' || impact.kind==='chief' || impact.kind==='manager') return touches(impact,['conversation_id']);
  return impact.kind==='conversation' && Boolean(presentation?.globalCounts || presentation?.review.currentConversationIds.has(impact.id)) && touches(impact,['attention']);
}
