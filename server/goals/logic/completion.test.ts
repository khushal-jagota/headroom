import { expect, it } from 'vitest';
import type { ChecklistBlock, ChecklistItem, PageDocument } from '../contracts.ts';
import { checklist_completed, effective_completion, selected_checklist } from './completion.ts';
const page = (checklist: ChecklistBlock): PageDocument => ({
  kind: 'headroom.page',
  version: 1,
  title: 'Approach',
  summary: '',
  blocks: [
    {
      type: 'section',
      id: 'section',
      title: 'Delivery',
      blocks: [
        {
          type: 'segmented',
          id: 'segments',
          views: [{ id: 'view', label: 'Checklist', blocks: [checklist] }],
        },
      ],
    },
  ],
});
it('selected checklist resolves nested identity only', () => {
  const checklist: ChecklistBlock = { type: 'checklist', id: 'delivery', items: [] },
    document = page(checklist);
  expect(selected_checklist(document, 'delivery')).toBe(checklist);
  for (const id of ['section', 'missing', null])
    expect(selected_checklist(document, id)).toBeNull();
});
it.each<{ items: ChecklistItem[]; completed: boolean }>([
  { items: [], completed: false },
  {
    items: [
      { id: 'draft', label: 'Draft', state: 'done' },
      { id: 'publish', label: 'Publish', state: 'done' },
    ],
    completed: true,
  },
  {
    items: [
      { id: 'draft', label: 'Draft', state: 'done' },
      { id: 'publish', label: 'Publish', state: 'open' },
    ],
    completed: false,
  },
  { items: [{ id: 'draft', label: 'Draft', state: 'blocked' }], completed: false },
])('completion requires nonempty all done $completed', ({ items, completed }) => {
  expect(checklist_completed({ type: 'checklist', id: 'delivery', items })).toBe(completed);
});
it('selected component overrides binary input', () => {
  const checklist: ChecklistBlock = {
      type: 'checklist',
      id: 'delivery',
      items: [{ id: 'draft', label: 'Draft', state: 'done' }],
    },
    document = page(checklist);
  expect(effective_completion(false, document, 'delivery')).toBe(true);
  checklist.items[0]!.state = 'open';
  expect(effective_completion(true, document, 'delivery')).toBe(false);
  expect(effective_completion(true, document, 'missing')).toBe(false);
  expect(effective_completion(true, document, null)).toBe(true);
  expect(effective_completion(false, document, null)).toBe(false);
});
