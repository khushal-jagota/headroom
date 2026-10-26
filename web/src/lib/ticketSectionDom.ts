import type { EffectiveNavigationView } from './navigation/knowledge';
import type { TicketSection } from './ticketSection';
import { ticketSectionFieldState, ticketSectionHeading, ticketSectionRunLabel } from './ticketSection';
import { fieldLabelFor, stageLabelFor } from './lifecycle';
import { holderLabelFor, holderOptionsFor, holderValue } from './ceilingHolder';
import { workerPageHistory } from './workerPageHistory';

/** Credit only public facts actually mounted in this Ticket section. */
export function ticketSectionDomMatches(root:HTMLElement,masthead:HTMLElement,section:TicketSection,view:EffectiveNavigationView):boolean {
  const row=section.record.value,lc=section.lifecycle,detail=section.detail;
  if(!masthead.isConnected || masthead.dataset.ticketMastheadId!==section.record.id) return false;
  const controls=masthead.querySelector('[data-ticket-lifecycle]');
  if(!controls) return false;
  const actions=controls.querySelectorAll('[data-ticket-backlog-action]'),markers=controls.querySelectorAll('[data-ticket-backlogged]');
  if(row.terminal) {
    if(actions.length!==0 || markers.length!==0 || controls.querySelector('.actions')) return false;
  } else if(actions.length!==1 || actions[0].textContent!==(row.is_backlogged ? 'Resume ticket' : 'Backlog ticket')
    || markers.length!==(row.is_backlogged ? 1 : 0) || (row.is_backlogged && markers[0].textContent!=='Backlogged')) return false;
  if(!root.isConnected || root.dataset.ticketSection!==section.record.id || root.dataset.sectionStage!==row.stage) return false;
  if(root.querySelector('[role=listbox]')) return false;
  const parent=detail.manager_id ? {id:detail.manager_id,title:detail.manager_title || 'Manager'} : null;
  // Admission proves this typed detail principal has the canonical kind and ID.
  const principal=detail.ceiling_holder;
  const holder=holderLabelFor(parent,principal);
  const reviewer=holderOptionsFor(parent,principal).find(option=>option.value===holderValue(principal))?.label;
  const current=root.querySelector('[data-worker-live-card]');
  if(row.gating_field===null) {
    if(current) return false;
  } else {
    if(!current) return false;
    const proposal=current.querySelector('[data-approval-block]');
    if(detail.pending_proposal?.field===row.gating_field) {
      if(proposal?.getAttribute('data-field')!==row.gating_field
        || current.getAttribute('data-proposal-created-at')!==String(row.proposal_created_at)
        || current.getAttribute('data-proposal-revision')!==String(row.proposal_revision)
        || proposal.querySelector('[data-reviewer-heading]')?.textContent!==reviewer) return false;
    } else {
      if(proposal || current.querySelector('h2')?.textContent!==`${ticketSectionHeading(section,view)} ${fieldLabelFor(lc,row.gating_field)}`) return false;
      if(row.stage!=='needs_brief' && current.querySelector('[data-scope-ceiling]')?.getAttribute('aria-label')!==`Approved until ${row.ceiling==='none' ? 'No further' : stageLabelFor(lc,row.ceiling)}, then ${holder} reviews`) return false;
    }
  }
  const fold=root.querySelector<HTMLDetailsElement>('[data-stage-fold]');
  if(!fold) return false;
  // A closed native fold attests its aggregate summary, never its hidden rows.
  const history=workerPageHistory(lc,detail);
  if(fold.querySelector('.band-count')?.textContent!==`${history.completed.length} of ${lc.fieldIds.length} done`) return false;
  if(!fold.open) return true;
  const fields=[...fold.querySelectorAll<HTMLElement>('[data-stage-state]')];
  return fields.length===history.fields.length && history.fields.every((field,index)=>{
    const element=fields[index],label=field===row.gating_field ? ticketSectionRunLabel(section,view) : null;
    return element.dataset.field===field && element.dataset.stageState===ticketSectionFieldState(section,view,field)
      && element.querySelector('.disclosure-stage-name')?.textContent===fieldLabelFor(lc,field)
      && (element.querySelector('[data-stage-run-label]')?.textContent ?? null)===label;
  });
}
