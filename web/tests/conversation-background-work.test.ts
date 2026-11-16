import { describe, expect, it } from 'vitest';
import { hasBackgroundTaskAfterTheTurn } from '../src/lib/conversation/transcript';

const task = { task_id: 't1', task_description: 'Review the diff' };

describe('background work counts as working', () => {
  it('is working only once the turn has ended, never during a turn or when activity is unknown', () => {
    expect(hasBackgroundTaskAfterTheTurn({ turn_running: false, background_tasks: [task] })).toBe(true);
    expect(hasBackgroundTaskAfterTheTurn({ turn_running: false, background_tasks: [] })).toBe(false);
    expect(hasBackgroundTaskAfterTheTurn({ turn_running: true, background_tasks: [task] })).toBe(false);
    expect(hasBackgroundTaskAfterTheTurn({ turn_running: null, background_tasks: [task] })).toBe(false);
    expect(hasBackgroundTaskAfterTheTurn(null)).toBe(false);
  });
});
