import { describe, expect, it } from 'vitest';
import type { TicketDetail } from '../src/lib/types';
import type { WorkerTypeManifest } from '../src/lib/lifecycle';
import type { TicketRecord } from '../src/lib/navigation/contracts';
import { createNavigationCollection } from './support/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { ticketSection, ticketSectionFacts, ticketSectionHeading, ticketSectionFieldState, ticketSectionRunLabel } from '../src/lib/ticketSection';
import { currentSnapshot, continuity, conversation } from './support/navigation/fixtures';

const record:(TicketRecord & {value:import('../src/lib/navigation/contracts').TicketValue})={kind:'ticket',id:'ticket',value:{title:'Ticket',recap:'',recap_preview:'',recap_updated_at:null,recap_stage_changed:false,worker_type:'coding',stage_label:'Plan',workflow_label:'Coding',stage:'needs_plan',
  gating_field:'plan',stage_ownership:'worker',terminal:false,ticket_status:'awaiting_approval',blocked:false,created_at:1,updated_at:2,
  worker_step_claim_out:false,deadline:null,project_id:null,manager_kind:'chief',manager_id:null,
  conversation_id:'conversation',is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'chief',id:'chief'},
  proposal_field:'plan',proposal_created_at:1,proposal_revision:2,machine_id:'75a49e78-97de-4e28-8dc5-79646761c46f',locale:'en'}};
const configuration:WorkerTypeManifest={worker_type:'coding',label:'Coding',stages:[
  {id:'needs_plan',label:'Plan',gating_field:'plan',ownership_mode:'worker',is_terminal:false},
  {id:'done',label:'Done',gating_field:null,ownership_mode:null,is_terminal:true}],
  fields:[{id:'plan',label:'Plan'}],advance:{needs_plan:'done'},ceiling_range:['needs_plan','done'],default_ceiling:'needs_plan',
  worker_profile_id:'coding',default_backend:'codex',default_model:null,default_reasoning_effort:null};
const detail:TicketDetail={id:'ticket',title:'Ticket',worker_type:'coding',stage:'needs_plan',ceiling:'needs_plan',
  ceiling_holder:{kind:'chief',id:'chief'},ticket_status:'awaiting_approval',is_backlogged:false,blocked:false,manager_id:null,
  conversation_id:'conversation',conversation_history:[],employee_backend:'codex',employee_launch_model:null,
  employee_launch_reasoning_effort:null,employee_configuration_editable:false,manager_title:null,
  field_values:{},pending_proposal:{field:'plan',body:'Exact body',proposed_by:'agent',created_at:1,revision:2}};

describe('Ticket section presentation',()=>{
  it('uses the loaded detail without a matching navigation or configuration tuple',()=>{
    const changed={...detail,stage:'done',pending_proposal:null};
    expect(ticketSection(changed,configuration)).toMatchObject({detail:changed,record:{value:{stage:'done',terminal:true,gating_field:null}}});
    const olderProposal={...detail,pending_proposal:{...detail.pending_proposal!,revision:1}};
    expect(ticketSection(olderProposal,configuration)?.detail).toBe(olderProposal);
    const changedConfiguration={...configuration,stages:configuration.stages.map(stage=>({...stage,gating_field:null}))};
    expect(ticketSection(detail,changedConfiguration)?.record.value.gating_field).toBeNull();
  });
  it('reprojects held section knowledge after same-publication runtime loss without erasing durable facts',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    try {
      adapter.publishSnapshot(currentSnapshot({...continuity,records:[record,conversation('conversation',0,true,false)]}),'section');
      const view=knowledge.accept(adapter.getPublication()!),section=ticketSection(detail,configuration)!;
      expect(ticketSectionHeading(section,view)).toBe('Waiting at');
      expect(ticketSectionFieldState(section,view,'plan')).toBe('current-awaiting-approval');
      const lost=knowledge.loseRuntime()!;
      expect(lost.publication).toBe(view.publication);
      // Runtime loss does not remove the loaded section.
      expect(ticketSection(detail,configuration)).toBeDefined();
      expect(ticketSectionHeading(section,lost)).toBe('Waiting at');
      expect(ticketSectionFieldState(section,lost,'plan')).toBe('current-awaiting-approval');
      expect(ticketSectionFacts(section,lost)).toMatchObject({awaiting_reply:true,awaiting_answer:null,agent_state:null,
        conversation_signal:{message_for_owner:true}});
      expect(ticketSectionFacts(section,{...lost,conversations:new Map()}).conversation_signal).toEqual({});
    } finally {adapter.destroy();}
  });
  it('describes a held Worker step as active only when its live state is working',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    try {
      const section=ticketSection({...detail,ticket_status:'agent',pending_proposal:null},configuration)!;
      const held={...record,value:{...record.value,ticket_status:'agent',proposal_field:null}};
      const idle=conversation('conversation',0,false,false);
      idle.value.attention.current!.signal.working=false;
      adapter.publishSnapshot(currentSnapshot({...continuity,records:[held,idle]}),'held-idle');
      expect(ticketSectionHeading(section,knowledge.accept(adapter.getPublication()!))).toBe('At');
      adapter.publishSnapshot(currentSnapshot({...continuity,revision:'1',records:[held,conversation('conversation',0,false,false)]}),'held-working');
      expect(ticketSectionHeading(section,knowledge.accept(adapter.getPublication()!))).toBe('Currently working on');
      expect(ticketSectionHeading(section,null)).toBe('At');
    } finally {adapter.destroy();}
  });
  it('keeps a parked proposal actionable while another Ticket blocks subsequent work',()=>{
    for (const ceiling_holder of [{kind:'owner',id:'owner'}, {kind:'chief',id:'chief'}] as const) {
      const userConfiguration={...configuration,stages:configuration.stages.map(stage=>stage.id==='needs_plan' ? {...stage,ownership_mode:'user' as const} : stage)};
      const section=ticketSection({...detail, blocked:true, ceiling_holder},userConfiguration)!;
      expect(ticketSectionHeading(section,null)).toBe('Waiting at');
      expect(ticketSectionFieldState(section,null,'plan')).toBe('current-awaiting-approval');
      expect(ticketSectionRunLabel(section,null)).toBe(ceiling_holder.kind==='owner' ? 'Awaiting approval' : null);
    }
  });
});
