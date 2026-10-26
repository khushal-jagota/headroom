import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import TicketStageSection from '../src/components/TicketStageSection.svelte';
import type { TicketFieldApproval } from '../src/lib/types';

function field(approval?: TicketFieldApproval): string {
  return render(TicketStageSection, { props: {
    name: 'plan', value: 'Agreed plan', ticketStage: 'done', stageState: 'completed',
    readerLeftItOpen: false, approval
  } }).body;
}

describe('Ticket field approval presentation', () => {
  it('keeps each approval visible in the collapsed field summary and keeps UI managers generic', () => {
    const approvals: TicketFieldApproval[] = [
      {method: 'explicit_accept', principal: {kind: 'owner', id: 'owner'}, display_label: 'Approved by Khushal'},
      {method: 'explicit_accept', principal: {kind: 'chief', id: 'chief'}, display_label: 'Approved by Chief'},
      {method: 'explicit_accept', principal: {kind: 'manager', id: 'item-private-id'}, display_label: 'Approved by Project release'},
      {method: 'auto_accept', principal: null, display_label: 'Auto-accepted'},
      {method: 'unknown', principal: null, display_label: 'Approval unknown'},
    ];
    const labels = ['Approved by Khushal', 'Approved by Chief', 'Approved by Manager', 'Auto-accepted', 'Approval unknown'];
    approvals.forEach((approval, index) => {
      const html = field(approval);
      expect(html.match(/<details[^>]*>/)?.[0]).not.toMatch(/\sopen(?:[\s=>]|$)/);
      expect(html.match(/<summary[^>]*>[\s\S]*?<\/summary>/)?.[0]).toContain(labels[index]);
      expect(html).not.toContain('Agreed plan');
      expect(html).not.toContain('data-markdown-inline-edit');
      expect(html).not.toContain('Project release');
      expect(html).not.toContain('item-private-id');
    });
    expect(field()).not.toContain('ticket-field-approval');
    expect(field()).not.toContain('Approval unknown');
  });
});
