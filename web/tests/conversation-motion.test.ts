import { afterEach, describe, expect, it, vi } from 'vitest';
import { conversationMotion } from '../src/lib/conversation/surfaceMotion';

afterEach(() => vi.unstubAllGlobals());
function rig(reduced = false) {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal('window', { matchMedia: () => ({ matches: reduced }) });
  vi.stubGlobal('performance', { now: () => 0 });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
  const pane = {} as HTMLElement;
  const node = { closest: () => pane } as unknown as HTMLElement;
  return { motion: conversationMotion(node), frame: (time: number) => frames.shift()!(time), frames };
}
describe('conversation surface frame ownership', () => {
  it('writes concurrent sizes before the viewport reads the same frame', async () => {
    const { motion, frame, frames } = rig();
    const order: string[] = [];
    const position = motion.move({}, t => order.push(`position:${t}`), 240, undefined, true);
    const first = motion.move({}, t => order.push(`tray:${t}`));
    const second = motion.move({}, t => order.push(`label:${t}`));
    motion.following(() => order.push('scroll'));
    expect(frames).toHaveLength(1);
    order.length = 0;
    frame(120);
    expect(order).toEqual(['tray:0.5', 'label:0.5', 'position:0.5', 'scroll']);
    frame(240);
    expect(await first).toBe(true);
    expect(await second).toBe(true);
    expect(await position).toBe(true);
  });
  it('cancels an old intent without leaving its awaiting reconciliation blocked', async () => {
    const { motion, frame } = rig();
    const key = {};
    const oldWrite = vi.fn();
    const old = motion.move(key, oldWrite);
    const replacement = motion.move(key, () => undefined);
    expect(await old).toBe(false);
    frame(240);
    expect(oldWrite).toHaveBeenCalledTimes(1);
    expect(await replacement).toBe(true);
    const cancelled = motion.move(key, () => undefined);
    motion.cancel(key);
    expect(await cancelled).toBe(false);
  });
  it('places reduced-motion sizes and scroll together without queued frames', async () => {
    const { motion, frames } = rig(true);
    const order: string[] = [];
    motion.following(() => order.push('scroll'));
    expect(await motion.move({}, t => order.push(`size:${t}`))).toBe(true);
    expect(order).toEqual(['size:1', 'scroll']);
    expect(frames).toHaveLength(0);
  });
});
