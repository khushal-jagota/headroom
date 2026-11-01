import { expect, it } from 'vitest';
import { createNavigationCollection } from '../src/lib/navigation/collection';
import { createNavigationKnowledge } from '../src/lib/navigation/knowledge';
import { navigationWorkspacePresentation } from '../src/lib/navigation/presentation';
import { currentSnapshot, continuity } from './support/navigation/fixtures';
import type { TicketRecord } from '../src/lib/navigation/contracts';
import { railTicketEligible } from '../src/lib/workspaceRail';
import { ticketCommandImpact } from '../src/lib/ticketNavigationImpact';

it('admits a diagnostic navigation record without valid stage or holder and keeps healthy Tickets visible',()=>{
  const broken:TicketRecord={kind:'ticket',id:'broken',value:{title:'Broken',recap:'',recap_preview:'',worker_type:'removed',stage:'done',ticket_status:'errored',
    integrity_error:{reason:'unknown Worker type'},deadline:null,project_id:null,manager_kind:'chief',manager_id:null,conversation_id:null,locale:'vps',
    updated_at:1,created_at:1,is_backlogged:true}};
  const healthy:TicketRecord={kind:'ticket',id:'healthy',value:{title:'Healthy',recap:'',recap_preview:'',worker_type:'coding',stage:'needs_implementation',ticket_status:'empty',blocked:false,
    updated_at:1,created_at:1,worker_step_claim_out:false,deadline:null,project_id:null,manager_kind:'chief',manager_id:null,conversation_id:null,is_backlogged:false,
    ceiling:'needs_implementation',ceiling_holder:{kind:'owner',id:'owner'},proposal_field:null,proposal_revision:0,proposal_created_at:null,stage_ownership:'worker',gating_field:'implementation',terminal:false,locale:'vps'}};
  const adapter=createNavigationCollection();
  try {
    adapter.publishSnapshot(currentSnapshot({...continuity,records:[broken,healthy]}),'broken-admission');
    const view=createNavigationKnowledge().accept(adapter.getPublication()!);
    const presentation=navigationWorkspacePresentation(view);
    expect(presentation.tickets.map(row=>row.id)).toEqual(['broken','healthy']);
    const diagnostic=presentation.tickets.find(row=>row.id==='broken')!;
    expect(diagnostic).toMatchObject({agent_state:'errored',awaiting_approval:false,assigned:false,integrity_error:{reason:'unknown Worker type'}});
    expect(railTicketEligible(diagnostic)).toBe(true);
    expect(ticketCommandImpact(view,'broken','scope').complete).toBe(false);
    expect(presentation.review.items).toEqual([]);
  } finally {adapter.destroy();}
});
