import { describe, expect, it } from 'vitest';
import { reconcileDisplaySlots } from '../src/lib/conversation/displaySlots';
import { outgoingMessagesByPlace, type OutgoingMessage } from '../src/lib/conversation/outgoing';
import type { ConversationViewRow } from '../src/lib/conversation/view/contracts';
const local: OutgoingMessage = {messageId:'send',content:[{piece:'text',text:'words'}],senderLabel:'owner',mode:'steer',sentAtUnixMilliseconds:1,knownFate:'nothing_yet'};
const row = (key: string, sequence: number, sender: string|null = null): ConversationViewRow => ({kind:'row',key,row_kind:sender?'prompt':'agent_message',sender_message_id:sender,source_sequence:sequence,order:{sequence,slot:0},changed_through_sequence:sequence,created_at:1,turn_key:null,work_group_key:null,fold_rule:'never',body:{kind:'inline',value:{kind:'agent_message',content:[{piece:'text',text:key}],to_owner:true}}});
describe('mounted message presentation',()=>{
 it('retains the original slot through a receipt, later messages, and a recorded replacement, then releases it at eviction',()=>{
   let slots=reconcileDisplaySlots([], [row('before',1)], [local],new Set());
   const original=slots[1]!;
   slots=reconcileDisplaySlots(slots,[row('before',1),row('later',3)],[],new Set(['send']));
   expect(slots.map(s=>s.key)).toEqual(['record:before','send','record:later']);
   slots=reconcileDisplaySlots(slots,[row('before',1),row('later',3),row('received',4,'send')],[],new Set(['send']));
   expect(slots[1]?.order).toEqual(original.order);
   expect(slots[1]?.item?.key).toBe('received');
   expect(slots.filter(s=>s.local!==null)).toHaveLength(1);
   slots=reconcileDisplaySlots(slots,[row('new-window',20)],[],new Set(['send']));
   expect(slots.map(s=>s.key)).toEqual(['record:new-window']);
 });
 it('resends within the same slot and restores failure without changing historical order',()=>{
   let slots=reconcileDisplaySlots([], [row('before',1)], [local],new Set());
   slots=reconcileDisplaySlots(slots,[row('before',1),row('after',2)],[{...local,messageId:'retry',displayMessageId:'send'}],new Set());
   expect(slots.map(s=>s.key)).toEqual(['record:before','send','record:after']);
   expect(slots[1]?.local?.messageId).toBe('retry');
   slots=reconcileDisplaySlots(slots,[row('before',1),row('after',2)],[local],new Set());
   expect(slots[1]?.local?.messageId).toBe('send');
   expect(reconcileDisplaySlots(slots,[row('before',1)],[],new Set()).map(s=>s.key)).toEqual(['record:before']);
 });
 it('keeps unanswered and waiting messages in the thread unless queue membership requires the tray',()=>{
   const messages=[{...local,knownFate:'answer_never_came_back' as const},{...local,messageId:'waiting',knownFate:'waiting_for_the_agent' as const}];
   expect(outgoingMessagesByPlace(messages,{composerStackMessageIds:[],serverHeldSenderMessageIds:new Set()}).thread).toEqual(messages);
   expect(outgoingMessagesByPlace(messages,{composerStackMessageIds:['send'],serverHeldSenderMessageIds:new Set(['waiting'])}).composerStack).toEqual(messages);
 });
});
