import type { CurrentAuthority, DegradedAuthority, Snapshot, CurrentSnapshotResponse, DegradedSnapshotResponse, RevisionBatch, BatchEnvelope, ConversationRecord, Continuity } from '../../../src/lib/navigation/contracts';
import { SCHEMA_VERSION, SUPPORTED_SEMANTICS, WIRE_VERSION } from '../../../src/lib/navigation/contracts';
import { encodeCursor } from '../../../src/lib/navigation/validation';

export const authority = (scope = 'signal-workspace', generation = '1', session = 'delivery'): CurrentAuthority => ({
  delivery_session_id:session,availability:{state:'current',generation,reason:null},
  permit:{scope,runtime_epoch:'producer',installation_id:'installed',generation},
});
export function currentSnapshot(snapshot: Snapshot, profile = authority()): CurrentSnapshotResponse {
  return {wire_version:WIRE_VERSION,mode:'current',authority:profile,cursor:encodeCursor(snapshot),snapshot,proof:null};
}
export function degradedSnapshot(snapshot: Snapshot, generation = '1', session = 'delivery'): DegradedSnapshotResponse {
  const profile: DegradedAuthority = {delivery_session_id:session,availability:{state:'unavailable',generation,reason:'lost'},permit:null};
  return {wire_version:WIRE_VERSION,mode:'degraded',authority:profile,cursor:null,snapshot:{...snapshot,records:snapshot.records.map(record => record.kind === 'conversation'
    ? {...record,value:{...record.value,attention:{current:null,durable:record.value.attention.durable}}} : record)},proof:null};
}
export const envelope = (batch: RevisionBatch, profile = authority()): BatchEnvelope => ({wire_version:WIRE_VERSION,authority:profile,batch});
export const continuity: Continuity = {scope:'workspace',schema_version:SCHEMA_VERSION,semantics:SUPPORTED_SEMANTICS,epoch:'e1',revision:'0'};
export function conversation(id = 'c', read = 0, message = true, failure = true): ConversationRecord {
  return {kind:'conversation',id,value:{latest_sequence:20,owner_read_through_sequence:read,
    first_unread_owner_message_at:message ? 1 : null,latest_agent_activity_sequence:20,latest_agent_activity_at:1,error_acknowledged_through_sequence:0,
    lifecycle_summary:{latest_turn:null,turn_status_counts:{intent:0,running:0},request_status_counts:{pending:0,answering:0},attempt_status_counts:{intent:0,unknown:0}},
    attention:{current:{signal:{working:true,message_for_owner:message},awaiting_answer:true,last_turn_failed:failure},durable:{message_for_owner:message,last_turn_failed:failure}}}};
}
