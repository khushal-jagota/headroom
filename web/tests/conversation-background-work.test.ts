import { describe, expect, it } from 'vitest';
import { backgroundTasksAfterTheTurn, backgroundWorkSentence } from '../src/lib/conversation/transcript';

const task = { task_id: 't1', task_description: 'Review the diff' };

describe('background work the conversation shows', () => {
  it('names live tasks only once the turn has ended, never during a turn or when activity is unknown', () => {
    expect(backgroundTasksAfterTheTurn({ turn_running: false, background_tasks: [task] })).toEqual([task]);
    expect(backgroundTasksAfterTheTurn({ turn_running: true, background_tasks: [task] })).toEqual([]);
    expect(backgroundTasksAfterTheTurn({ turn_running: null, background_tasks: [task] })).toEqual([]);
    expect(backgroundTasksAfterTheTurn(null)).toEqual([]);
  });
  it('says each task by its own description', () => {
    expect(backgroundWorkSentence(['Review the diff'])).toBe('Background work running: Review the diff');
    expect(backgroundWorkSentence(['Review the diff', 'Check the build']))
      .toBe('Background work running: Review the diff, Check the build');
  });
});
