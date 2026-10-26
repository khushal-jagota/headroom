import { describe, expect, it } from 'vitest';
import { currentPlacement, streamArrivalBudget, streamTravelFits, threadPlacement } from '../src/components/conversation/viewport/placement';

describe('conversation placement causes', () => {
  it('allows a multi-line arrival but cannot spend its budget on a larger layout gap', () => {
    const arrival = streamArrivalBudget(true, true, 1000, 1072);
    expect(streamTravelFits(72, arrival)).toBe(true);
    expect(streamTravelFits(48, arrival - 24)).toBe(true);
    expect(streamTravelFits(24, arrival - 48)).toBe(true);
    expect(streamTravelFits(240, arrival)).toBe(false);
    expect(streamTravelFits(24, streamArrivalBudget(false, true, 1000, 1240))).toBe(false);
    expect(streamTravelFits(24, streamArrivalBudget(true, false, 1000, 1072))).toBe(false);
    // The scroll writer ignores subpixel residuals. Re-admission must not spin
    // reconciliation forever when no browser movement can consume the budget.
    expect(streamTravelFits(.25, arrival)).toBe(false);
  });
  it('does not admit a stale reader or a canceled placement after deferred rendering', () => {
    const original = {};
    const operation = threadPlacement(original, 'sent', 'message');
    expect(currentPlacement(operation, original)).toBe(true);
    expect(currentPlacement(operation, {})).toBe(false);
    operation.cancelled = true;
    expect(currentPlacement(operation, original)).toBe(false);
  });
});

it('copies an anchor from reactive proxies without retaining mutable address fields', async () => {
  const { copyViewReaderAnchor } = await import('../src/lib/conversation/view/copyAnchor');
  const source = {key:'row',order:{sequence:8,slot:0},offset:-12,following:false,
    content:{pieceIndex:0,lineOffset:9,text:{contentIdentity:{algorithm:'sha256' as const,digest:'exact',utf16Length:7},blockIndex:3,textLeafPath:[0,2],utf16Offset:1}}};
  const proxy = new Proxy(source, {});
  expect(() => structuredClone(proxy)).toThrow();
  const saved = copyViewReaderAnchor(proxy);
  source.order.sequence = 99;
  source.content.text.textLeafPath[0] = 8;
  source.content.text.contentIdentity.digest = 'changed';
  expect(saved.order.sequence).toBe(8);
  expect(saved.content?.text.textLeafPath).toEqual([0,2]);
  expect(saved.content?.text.contentIdentity.digest).toBe('exact');
  expect(Object.isFrozen(saved.content?.text)).toBe(true);
});
