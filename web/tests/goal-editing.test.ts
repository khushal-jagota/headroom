import { describe, expect, it, vi } from 'vitest';
import { createGoalEditor } from '../src/lib/goals/editing';
import { createGoalRequestSessions } from '../src/lib/goals/requestSessions';
import type { ChecklistBlock, GoalEditBody, GoalResponse, ManualWriteBody } from '../src/lib/goals/contracts';

function goal(id: string): GoalResponse {
  return { id, revision: 1, page: { kind: 'headroom.page', version: 1, title: id, summary: '', blocks: [
    { id: 'steps', type: 'checklist', items: [{ id: 'one', label: 'One', state: 'open' }, { id: 'two', label: 'Two', state: 'open' }] },
  ] } } as GoalResponse;
}
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const done = (itemId: string) => (block: ChecklistBlock): ChecklistBlock => ({ ...block, items: block.items.map(item => item.id === itemId ? { ...item, state: 'done' } : item) });

describe('goal writes across control lifetimes', () => {
  it('serializes editors for the captured goal, applies fresh documents, and lets another goal proceed', async () => {
    const saved = new Map([['first', goal('first')], ['second', goal('second')]]), gate = deferred<void>();
    const writes: Array<[string, GoalEditBody]> = [];
    const io = {
      read: async (id: string) => saved.get(id)!,
      write: async (id: string, body: GoalEditBody) => {
        writes.push([id, body]);
        if (writes.length === 1) await gate.promise;
        const accepted = { ...saved.get(id)!, ...body, revision: saved.get(id)!.revision + 1 };
        saved.set(id, accepted); return accepted;
      }, refresh: async () => undefined,
    };
    const first = createGoalEditor('first', io), reopened = createGoalEditor('first', io);
    const one = first.updateBlock('steps', block => done('one')(block as ChecklistBlock));
    const two = reopened.updateBlock('steps', block => done('two')(block as ChecklistBlock));
    await createGoalEditor('second', io).write({ completed: true });
    expect(writes.map(([id]) => id)).toEqual(['first', 'second']);
    gate.resolve(); await Promise.all([one, two]);
    expect(writes[2][1].expected_revision).toBe(2);
    expect((writes[2][1].page!.blocks[0] as ChecklistBlock).items.map(item => item.state)).toEqual(['done', 'done']);
  });

  it('keeps accepted writes successful when refresh fails, rejects absent blocks, and preserves explicit stale revisions', async () => {
    const write = vi.fn(async (_id: string, body: GoalEditBody) => ({ ...goal('captured'), ...body, revision: 2 }));
    const editor = createGoalEditor('captured', { read: async () => goal('captured'), write, refresh: async () => { throw new Error('Read unavailable'); } });
    await expect(editor.write({ completed: true }, 0)).resolves.toMatchObject({ completed: true, revision: 2 });
    expect(write.mock.calls[0][1].expected_revision).toBe(0);
    await expect(editor.updateBlock('removed', block => block)).rejects.toThrow('no longer exists');
    expect(write).toHaveBeenCalledTimes(1);
    const stale = vi.fn(async () => { throw new Error('Stale revision'); });
    const rejected = createGoalEditor('captured', { read: async () => goal('captured'), write: stale, refresh: async () => undefined });
    await expect(rejected.write({ completed: false }, 0)).rejects.toThrow('Stale revision');
    expect(stale).toHaveBeenCalledTimes(1);
  });

  it('restores late failures by control identity and retries the exact manual receipt only on request', async () => {
    const sessions = createGoalRequestSessions(), gate = deferred<void>();
    const body: ManualWriteBody = { request_id: 'original', operation: 'replace', date: '2026-10-30', basis: 'date', value: 7, expected_definition_revision: 1, expected_write_revision: 3 };
    const session = sessions.manual('first', 'reading'), write = vi.fn(() => gate.promise);
    const pending = session.run(body, write);
    expect(sessions.manual('first', 'reading')).toBe(session);
    expect(sessions.manual('other', 'reading').current).toBeUndefined();
    expect(sessions.manual('first', 'other').current).toBeUndefined();
    session.discard(); expect(session.current?.status).toBe('pending');
    const failure = expect(pending).rejects.toThrow('Offline'); gate.reject(new Error('Offline')); await failure;
    const reopened = sessions.manual('first', 'reading');
    expect(reopened.current).toMatchObject({ status: 'failed', command: body });
    expect(write).toHaveBeenCalledTimes(1);
    const retry = reopened.current!.command, retryWrite = vi.fn(async () => undefined);
    await reopened.run(retry, () => retryWrite());
    expect(retry).toBe(body); expect(reopened.current).toBeUndefined();
    expect(retryWrite).toHaveBeenCalledTimes(1);
  });

  it('bounds sessions without evicting pending commands or failed receipts', async () => {
    const sessions = createGoalRequestSessions(2), failed = sessions.binary('failed');
    await expect(failed.run({ completed: true }, async () => { throw new Error('Offline'); })).rejects.toThrow('Offline');
    const retained = sessions.manual('a', 'pending'), gate = deferred<void>();
    const body: ManualWriteBody = { request_id: 'pending', date: '2026-10-30', expected_definition_revision: 1, basis: 'date', operation: 'increment', delta: 1 };
    const attempt = retained.run(body, () => gate.promise);
    const refused = sessions.binary('new'), operation = vi.fn(async () => undefined);
    expect(refused.current).toBeUndefined();
    await expect(refused.run({ completed: true }, operation)).rejects.toThrow('Resolve or discard');
    expect(operation).not.toHaveBeenCalled();
    expect(sessions.binary('failed')).toBe(failed);
    expect(sessions.manual('a', 'pending')).toBe(retained);
    await failed.run(failed.current!.command, async () => undefined);
    await refused.run({ completed: true }, operation);
    expect(operation).toHaveBeenCalledTimes(1);
    gate.resolve(); await attempt;
  });

  it('retains the newer item command when an older attempt fails late', async () => {
    const session = createGoalRequestSessions().checklist('goal', 'steps', 'one');
    const first = deferred<void>(), second = deferred<void>();
    const old = session.run({ state: 'done' }, () => first.promise);
    const latest = session.run({ state: 'open' }, () => second.promise);
    const rejected = expect(old).rejects.toThrow('First attempt failed');
    first.reject(new Error('First attempt failed')); await rejected;
    expect(session.current).toEqual({ status: 'pending', command: { state: 'open' } });
    second.resolve(); await latest;
    expect(session.current).toBeUndefined();
  });
});


describe('checklist date drafts and receipts',()=>{
 it('retains invalid native drafts before submission, keeps their revision, and bounds their owner',()=>{
  const sessions=createGoalRequestSessions(1),date=sessions.checklistDeadline('goal','steps','one');
  const changed=vi.fn();
  date.subscribe(changed);
  date.begin('2026-10-30',4);
  const invalid=new Error('Enter a complete date.');
  date.update('',invalid);
  date.begin('2026-11-01',9);
  expect(sessions.checklistDeadline('goal','steps','one')).toBe(date);
  expect(date.draft).toMatchObject({value:'',initialValue:'2026-10-30',revision:4,error:invalid,dirty:true});
  expect(date.current).toBeUndefined();
  expect(changed).toHaveBeenCalledTimes(2);
  const other=sessions.checklistDeadline('other','steps','one');
  expect(()=>other.begin('',1)).toThrow('Resolve or discard');
  date.discard();
  other.begin('',1);other.update('2026-11-02');
  expect(other.draft).toMatchObject({value:'2026-11-02',revision:1,error:null});
  // The evicted old handle must regain admission before it accepts a new dirty draft.
  expect(()=>date.begin('2026-10-30',9)).toThrow('Resolve or discard');
  other.discard();
  date.begin('2026-10-30',9);
  expect(date.draft?.revision).toBe(9);
 });

 it('restores date failures independently from completion and retries only the captured command',async()=>{
  const sessions=createGoalRequestSessions(),date=sessions.checklistDeadline('goal','steps','one');
  date.begin('2026-10-30',3);date.update('2026-11-01');
  const command={deadline:'2026-11-01',revision:date.draft!.revision},gate=deferred<void>();
  const write=vi.fn(()=>gate.promise);
  const pending=date.run(command,write);
  date.discard();
  expect(date.current?.status).toBe('pending');
  expect(date.draft?.value).toBe('2026-11-01');
  await sessions.checklist('goal','steps','one').run({state:'done'},async()=>undefined);
  const failure=expect(pending).rejects.toThrow('Stale revision');
  gate.reject(new Error('Stale revision'));await failure;
  const reopened=sessions.checklistDeadline('goal','steps','one');
  expect(reopened.current).toMatchObject({command,status:'failed'});
  reopened.begin('2026-11-02',7);
  expect(reopened.draft?.revision).toBe(3);
  expect(write).toHaveBeenCalledTimes(1);
  const retry=vi.fn(async()=>undefined);
  await reopened.run(reopened.current!.command,retry);
  expect(retry).toHaveBeenCalledTimes(1);
  expect(reopened.current).toBeUndefined();expect(reopened.draft).toBeUndefined();
  expect(sessions.checklist('other','steps','one').current).toBeUndefined();
  expect(sessions.checklistDeadline('goal','other','one').draft).toBeUndefined();
 });

 it('keeps a newer dirty date when an older submitted edit finishes',async()=>{
  const date=createGoalRequestSessions().checklistDeadline('goal','steps','one'),gate=deferred<void>();
  date.begin('',1);date.update('2026-10-30');
  const pending=date.run({deadline:'2026-10-30',revision:1},()=>gate.promise);
  date.update('2026-11-01');
  gate.resolve();await pending;
  expect(date.draft).toMatchObject({value:'2026-11-01',revision:1,dirty:true});
  date.discard();expect(date.draft).toBeUndefined();
 });

 it('applies queued date and completion commands to accepted documents without erasing neighboring content',async()=>{
  let saved=goal('goal');
  const io={
   read:async()=>saved,
   write:async(_id:string,body:GoalEditBody)=>{
    if(body.expected_revision!==saved.revision)throw new Error('Stale revision');
    saved={...saved,...body,revision:saved.revision+1};return saved;
   },refresh:async()=>undefined,
  };
  const editor=createGoalEditor('goal',io),date=createGoalRequestSessions().checklistDeadline('goal','steps','one');
  date.begin('',saved.revision);date.update('2026-10-30');
  const command={deadline:date.draft!.value,revision:date.draft!.revision};
  const setDate=(deadline:string|null)=>(block:import('../src/lib/goals/contracts').Block)=>{
   if(block.type!=='checklist')throw new Error('Checklist missing');
   return {...block,items:block.items.map(item=>item.id==='one'?{...item,deadline}:item)};
  };
  const dated=date.run(command,()=>editor.updateBlock('steps',setDate(command.deadline),command.revision));
  const completed=editor.updateBlock('steps',block=>done('one')(block as ChecklistBlock));
  await Promise.all([dated,completed]);
  expect(saved.page.blocks[0]).toMatchObject({id:'steps',items:[{id:'one',label:'One',state:'done',deadline:'2026-10-30'},{id:'two',state:'open'}]});
  await editor.updateBlock('steps',setDate(null),saved.revision);
  expect(saved.page.blocks[0]).toMatchObject({items:[{id:'one',state:'done',deadline:null},{id:'two',state:'open'}]});
  await expect(editor.updateBlock('steps',setDate('2026-11-01'),command.revision)).rejects.toThrow('Stale revision');
  expect(saved.page.blocks[0]).toMatchObject({items:[{deadline:null},{}]});
 });
});
