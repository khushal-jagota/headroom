import type { ViewFrame, ViewSnapshot } from '../contracts';
import { recordKey, sameAuthority } from '../identity';

export function sameDomain(left:ViewSnapshot['coverage'],right:ViewSnapshot['coverage']) {
  return left.scope===right.scope && left.schema_version===right.schema_version && left.semantics===right.semantics && left.epoch===right.epoch;
}
export function applyFrame(previous:ViewSnapshot | undefined,frame:ViewFrame):ViewSnapshot {
  if(frame.event==='reset') throw new Error('View reset required');
  if(frame.event==='snapshot') return frame;
  if(!previous || previous.mode!=='current' || !sameAuthority(previous.authority,frame.authority)
    || previous.view_id!==frame.view_id || previous.scope_generation!==frame.scope_generation
    || previous.ordering_generation!==frame.ordering_generation || !sameDomain(previous.coverage,frame.coverage)
    || previous.coverage.revision!==frame.previous_coverage.revision) throw new Error('Obsolete view frame');
  if(frame.event==='checkpoint') {
    if(frame.content_version!==previous.content_version) throw new Error('Checkpoint changed content');
    return {...previous,coverage:frame.coverage};
  }
  if(frame.previous_content_version!==previous.content_version || frame.content_version===previous.content_version) throw new Error('View content gap');
  const records=new Map(previous.content.records.map(row=>[recordKey(row),row]));
  for(const key of frame.evictions) records.delete(recordKey(key));
  for(const row of frame.upserts) records.set(recordKey(row),row);
  const content={records:[...records.values()],windows:frame.windows,counts:frame.counts,review_context:frame.review_context,review_summary:frame.review_summary};
  return {...previous,coverage:frame.coverage,content_version:frame.content_version,content};
}
