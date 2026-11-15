import { beforeEach, describe, expect, it, vi } from 'vitest';
const animation = vi.hoisted(() => ({ finishFade: () => {}, finishCollapse: () => {} }));
vi.mock('../src/lib/conversation/surfaceMotion', () => ({
  fadeConversationExit: vi.fn((_node: HTMLElement, signal: AbortSignal) => new Promise<void>(resolve => {
    animation.finishFade = resolve;
    signal.addEventListener('abort', () => resolve(), {once:true});
  })),
  collapseConversationRow: vi.fn((_node: HTMLElement, signal: AbortSignal) => new Promise<() => void>(resolve => {
    animation.finishCollapse = () => resolve(() => {});
    signal.addEventListener('abort', () => resolve(() => {}), {once:true});
  }))
}));
import { MessageExits } from '../src/lib/conversation/messageExits';
import { collapseConversationRow, fadeConversationExit } from '../src/lib/conversation/surfaceMotion';
const slot = {key:'local',local:{messageId:'send'},item:null};
describe('message exit ownership',()=>{
  beforeEach(()=>vi.clearAllMocks());
  it('cancels a fading removal immediately when retry restoration returns the same slot',async()=>{
    const exits=new MessageExits<typeof slot>(); const changed=vi.fn();
    exits.register(slot.key,{style:{cssText:''}} as HTMLElement);
    expect(exits.retain([], [slot],changed)).toEqual([slot]);
    const signal=vi.mocked(fadeConversationExit).mock.calls[0]![1]!;
    expect(exits.retain([slot],[slot],changed)).toEqual([slot]);
    expect(signal.aborted).toBe(true);
    await Promise.resolve();
    expect(collapseConversationRow).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
  });
  it('cancels an active collapse on unmount and never publishes into a disposed transcript',async()=>{
    const exits=new MessageExits<typeof slot>(); const changed=vi.fn();
    const unregister=exits.register(slot.key,{style:{cssText:''}} as HTMLElement);
    exits.retain([], [slot],changed);
    animation.finishFade(); await Promise.resolve();
    expect(collapseConversationRow).toHaveBeenCalledTimes(1);
    const signal=vi.mocked(collapseConversationRow).mock.calls[0]![1]!;
    unregister();
    expect(signal.aborted).toBe(true);
    await Promise.resolve();
    expect(changed).not.toHaveBeenCalled();
  });
  it('runs exactly one exit while repeated reconciliation retains a departing row',async()=>{
    const exits=new MessageExits<typeof slot>(); const changed=vi.fn();
    exits.register(slot.key,{style:{cssText:''}} as HTMLElement);
    const retained=exits.retain([], [slot],changed);
    exits.retain([],retained,changed);
    expect(fadeConversationExit).toHaveBeenCalledTimes(1);
    animation.finishFade(); await Promise.resolve();
    animation.finishCollapse(); await Promise.resolve();
    expect(changed).toHaveBeenCalledTimes(1);
    expect(exits.retain([],retained,changed)).toEqual([]);
  });
});
