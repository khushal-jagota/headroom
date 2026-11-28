import { describe, expect, it } from 'vitest';
import { managerGroupDefaultOpen } from '../src/lib/managerChildren';
import { WORK_ITEM_TICKET_GROUPS } from '../src/lib/workItemPresentation';

describe('Manager page group defaults', () => {
  it('opens only the groups that need the owner', () => {
    const open = WORK_ITEM_TICKET_GROUPS.filter(group => managerGroupDefaultOpen(group.key)).map(group => group.key);
    expect(open).toEqual(['awaiting_approval', 'awaiting_answer', 'assigned']);
  });
});
