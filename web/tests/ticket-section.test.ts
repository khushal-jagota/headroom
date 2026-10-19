import { describe, expect, it } from 'vitest';
import type { TicketDetail } from '../src/lib/types';
import type { WorkerTypeManifest } from '../src/lib/lifecycle';
import type { TicketRecord } from '../src/lib/navigation/contracts';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { admitTicketSection, ticketSectionFacts, ticketSectionHeading, ticketSectionFieldState } from '../src/lib/ticketSection';
import { currentSnapshot, continuity, conversation } from './support/navigation/fixtures';

const record:TicketRecord={kind:'ticket',id:'ticket',value:{title:'Ticket',recap_preview:'',worker_type:'coding',stage:'needs_plan',
  gating_field:'plan',stage_ownership:'worker',terminal:false,ticket_status:'awaiting_approval',blocked:false,created_at:1,updated_at:2,
  worker_step_claim_out:false,deadline:null,project_id:null,orchestrator_kind:'chief',sprint_item_id:null,
  conversation_id:'conversation',is_backlogged:false,ceiling:'needs_plan',ceiling_holder:{kind:'chief',id:'chief'},
  proposal_field:'plan',proposal_created_at:1,proposal_revision:2,locale:'en'}};
const configuration:WorkerTypeManifest={worker_type:'coding',label:'Coding',stages:[
  {id:'needs_plan',label:'Plan',gating_field:'plan',ownership_mode:'worker',is_terminal:false},
  {id:'done',label:'Done',gating_field:null,ownership_mode:null,is_terminal:true}],
  fields:[{id:'plan',label:'Plan'}],advance:{needs_plan:'done'},ceiling_range:['needs_plan','done'],default_ceiling:'needs_plan',
  worker_profile_id:'coding',default_backend:'codex',default_model:null,default_reasoning_effort:null};
const detail:TicketDetail={id:'ticket',title:'Ticket',worker_type:'coding',stage:'needs_plan',ceiling:'needs_plan',
  ceiling_holder:{kind:'chief',id:'chief'},ticket_status:'awaiting_approval',is_backlogged:false,blocked:false,sprint_item_id:null,
  conversation_id:'conversation',conversation_history:[],employee_backend:'codex',employee_launch_model:null,
  employee_launch_reasoning_effort:null,employee_configuration_editable:false,sprint_item_title:null,
  field_values:{},pending_proposal:{field:'plan',body:'Exact body',proposed_by:'agent',created_at:1,revision:2}};

describe('Ticket section admission',()=>{
  it('keeps structural/proposal/configuration identity together, including explicit proposal absence',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    try {
      adapter.publishSnapshot(currentSnapshot({...continuity,records:[record,conversation('conversation')]}),'section');
      const view=knowledge.accept(adapter.getPublication()!);
      expect(admitTicketSection(view,record,detail,configuration)?.detail).toBe(detail);
      expect(admitTicketSection(view,record,{...detail,stage:'done'},configuration)).toBeUndefined();
      expect(admitTicketSection(view,record,{...detail,pending_proposal:{...detail.pending_proposal!,revision:1}},configuration)).toBeUndefined();
      expect(admitTicketSection(view,record,detail,{...configuration,stages:configuration.stages.map(stage=>({...stage,gating_field:null}))})).toBeUndefined();
      const absent={...record,value:{...record.value,proposal_field:null,proposal_created_at:null,ticket_status:'empty'}};
      adapter.publishSnapshot(currentSnapshot({...continuity,revision:'1',records:[absent,conversation('conversation')]}),'absent');
      const absentView=knowledge.accept(adapter.getPublication()!);
      expect(admitTicketSection(absentView,absent,{...detail,ticket_status:'empty',pending_proposal:null},configuration)).toBeDefined();
      expect(admitTicketSection(view,record,{...detail,pending_proposal:null},configuration)).toBeUndefined();
    } finally {adapter.destroy();}
  });
  it('reprojects held section knowledge after same-publication runtime loss without erasing durable facts',()=>{
    const adapter=createNavigationCollection(),knowledge=createNavigationKnowledge();
    try {
      adapter.publishSnapshot(currentSnapshot({...continuity,records:[record,conversation('conversation',0,true,false)]}),'section');
      const view=knowledge.accept(adapter.getPublication()!),section=admitTicketSection(view,record,detail,configuration)!;
      expect(ticketSectionHeading(section,view)).toBe('Currently working on');
      expect(ticketSectionFieldState(section,view,'plan')).toBe('current-running');
      const lost=knowledge.loseRuntime()!;
      expect(lost.publication).toBe(view.publication);
      // Runtime loss cannot reject an unchanged structurally admitted action.
      expect(admitTicketSection(lost,record,detail,configuration)).toBeDefined();
      expect(ticketSectionHeading(section,lost)).toBe('Waiting at');
      expect(ticketSectionFieldState(section,lost,'plan')).toBe('current-awaiting-approval');
      expect(ticketSectionFacts(section,lost)).toMatchObject({awaiting_reply:true,awaiting_answer:null,agent_state:null,
        conversation_signal:{message_for_owner:true}});
      expect(ticketSectionFacts(section,{...lost,conversations:new Map()}).conversation_signal).toEqual({});
    } finally {adapter.destroy();}
  });
});
