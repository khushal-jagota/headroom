import {VIEW_PROJECTION_VERSION, wireDescriptors, type ViewFrame, type ViewSnapshot, type ViewContent, type ViewRecord} from '../contracts';
import {validate,continuity,authority,compareRevision,NavigationProtocolError,recordKey,sameAuthority} from '../validation';

export function parseFrame(input:unknown):ViewFrame {
  validate(input,{ref:'ViewFrame'},'ViewFrame');
  const frame=input as ViewFrame;
  if(frame.event==='reset') return frame;
  if(frame.projection_version!==VIEW_PROJECTION_VERSION) throw new NavigationProtocolError('Unsupported view projection version');
  continuity(frame.coverage); authority(frame.authority);
  if(!frame.view_id || !frame.content_version || !frame.ordering_generation || frame.scope_generation<0) throw new NavigationProtocolError('Invalid view identity');
  if(frame.event==='snapshot') validateContent(frame.content,frame.mode);
  else {
    continuity(frame.previous_coverage);
    if(!sameDomain(frame.coverage,frame.previous_coverage) || compareRevision(frame.coverage.revision,frame.previous_coverage.revision)<=0) throw new NavigationProtocolError('Invalid view coverage interval');
  }
  return frame;
}
export function sameDomain(left:ViewSnapshot['coverage'],right:ViewSnapshot['coverage']) {
  return left.scope===right.scope && left.schema_version===right.schema_version && left.semantics===right.semantics && left.epoch===right.epoch;
}
export function validateContent(content:ViewContent,mode:'current'|'degraded') {
  if(content.records.length>512 || content.windows.length>160) throw new NavigationProtocolError('View budget exceeded');
  const keys=new Set<string>();
  for(const row of content.records) {
    const key=recordKey(row);if(keys.has(key)) throw new NavigationProtocolError('Duplicate view row');keys.add(key);
    if(row.kind==='conversation' && (mode==='current') !== (row.value.attention.current!==null)) throw new NavigationProtocolError('Contradictory attention profile');
    if(row.kind==='conversation' && row.value.attention.current && (row.value.attention.current.signal.message_for_owner!==row.value.attention.durable.message_for_owner || row.value.attention.current.last_turn_failed!==row.value.attention.durable.last_turn_failed)) throw new NavigationProtocolError('Contradictory durable attention');
  }
  const contexts=new Set<string>();
  if(content.review_context.length>256)throw new NavigationProtocolError('Review context budget exceeded');
  for(const context of content.review_context) {
    if(contexts.has(context.conversation_id))throw new NavigationProtocolError('Duplicate review context');
    contexts.add(context.conversation_id);
    if(context.canonical_address_id===null)continue;
    const address=content.records.find(row=>row.kind==='address' && row.id===context.canonical_address_id);
    if(address?.kind!=='address' || address.value.conversation_id!==context.conversation_id
      || !keys.has(recordKey({kind:'conversation',id:context.conversation_id}))
      || !keys.has(recordKey({kind:address.value.owner_kind,id:address.value.owner_id})))throw new NavigationProtocolError('Missing review context dependency');
  }
  const windows=new Set<string>();
  for(const window of content.windows) {
    if(windows.has(window.window_id) || window.total<window.members.length || window.members.length>60 || window.members.length>window.request.limit) throw new NavigationProtocolError('Invalid view membership');
    windows.add(window.window_id);const members=new Set<string>();
    for(const key of window.members) {const id=recordKey(key);if(!keys.has(id)||members.has(id))throw new NavigationProtocolError('Missing or duplicate window member');members.add(id);}
  }
  if(Object.values(content.counts).some(value=>value!==null && value<0) || mode==='degraded' && content.counts.running_conversations!==null) throw new NavigationProtocolError('Invalid global count profile');
}
export function applyFrame(previous:ViewSnapshot | undefined,frame:ViewFrame):ViewSnapshot {
  if(frame.event==='reset') throw new NavigationProtocolError('View reset required');
  if(frame.projection_version!==VIEW_PROJECTION_VERSION) throw new NavigationProtocolError('Unsupported view projection version');
  if(frame.event==='snapshot') return frame;
  if(!previous || previous.mode!=='current' || !sameAuthority(previous.authority,frame.authority)
    || previous.view_id!==frame.view_id || previous.scope_generation!==frame.scope_generation
    || previous.ordering_generation!==frame.ordering_generation || !sameDomain(previous.coverage,frame.coverage)
    || previous.coverage.revision!==frame.previous_coverage.revision) throw new NavigationProtocolError('Obsolete view frame');
  if(frame.event==='checkpoint') {
    if(frame.content_version!==previous.content_version) throw new NavigationProtocolError('Checkpoint changed content');
    return {...previous,coverage:frame.coverage};
  }
  if(frame.previous_content_version!==previous.content_version || frame.content_version===previous.content_version) throw new NavigationProtocolError('View content gap');
  const records=new Map(previous.content.records.map(row=>[recordKey(row),row]));
  const touched=new Set<string>();
  for(const key of frame.evictions) {const id=recordKey(key);if(touched.has(id)||!records.has(id))throw new NavigationProtocolError('Invalid view eviction');touched.add(id);records.delete(id);}
  for(const row of frame.upserts) {const id=recordKey(row);if(touched.has(id))throw new NavigationProtocolError('Duplicate view operation');touched.add(id);records.set(id,row);}
  const content={records:[...records.values()],windows:frame.windows,counts:frame.counts,review_context:frame.review_context};
  validateContent(content,'current');
  return {...previous,coverage:frame.coverage,content_version:frame.content_version,content};
}
