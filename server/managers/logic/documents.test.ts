import { expect, test } from 'vitest';
import { prepare_plain_document } from './documents.ts';
import { prepare_document } from '../../goals/logic/documents.ts';

test('holder and goal documents reject a nested Tickets block', () => {
  const page = {
    kind: 'headroom.page',
    version: 1,
    title: '',
    summary: '',
    blocks: [
      { type: 'section', id: 'section', title: '', blocks: [{ type: 'tickets', id: 'tickets' }] },
    ],
  };
  expect(() => prepare_plain_document(page, () => 'unused')).toThrow();
  expect(() => prepare_document(page, () => 'unused')).toThrow();
});
