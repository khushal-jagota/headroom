import { describe, expect, it } from 'vitest';
import { retirementGeometry } from '../src/components/conversation/viewport/threadGeometry';
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

// These cases protect browser range support, which placement identity tests do not cover.
describe('reply tail retirement range', () => {
  it('drains upward travel and reply growth without adding downward range', () => {
    const first = retirementGeometry(1839, 500, 2031, 408, -8)!;
    expect(first).toEqual({top:1831, tail:308, insetDelta:-8});
    const second = retirementGeometry(1631, 500, 2023, first.tail)!;
    expect(second.tail).toBe(108);
    const complete = retirementGeometry(1511, 500, 2023, second.tail)!;
    expect(complete.tail).toBe(0);
    expect(retirementGeometry(1831, 500, 2103, first.tail)?.tail).toBe(228);
    expect(retirementGeometry(1831, 500, 2423, first.tail)?.tail).toBe(0);
    for (const result of [first, second]) {
      expect(Math.round(2023 + result.tail) - 500).toBe(result.top);
    }
  });
  it('represents the entire sub-gap residual and exact zero without a fixed gap floor', () => {
    for (let residual = 0; residual <= 24; residual++) {
      const result = retirementGeometry(1523 + residual, 500, 2023, 408)!;
      expect(result.tail).toBe(residual);
      expect(Math.round(2023 + result.tail) - 500).toBe(result.top);
    }
  });
  it('keeps fractional support within the prior budget and browser range precision', () => {
    const result = retirementGeometry(1523.25, 500, 2023.125, 24.25)!;
    expect(result.tail).toBe(0.875);
    expect(Math.round(2023.125 + result.tail) - 500).toBeGreaterThanOrEqual(result.top);
    expect(Math.round(2023.125 + result.tail) - 500 - result.top).toBeLessThan(1);
    expect(retirementGeometry(1523.25, 500, 2023.125, 0.125)).toBeNull();
    // The rounded real range already supports this fractional offset without a tail.
    expect(retirementGeometry(1523.5, 500, 2023.75, 0)?.tail).toBe(0);
  });
  it('defers unsafe inset normalization at history top and current maximum', () => {
    expect(retirementGeometry(4, 500, 2031, 408, -8)?.insetDelta).toBe(0);
    expect(retirementGeometry(1939, 500, 2031, 408, -8)?.insetDelta).toBe(0);
    expect(retirementGeometry(1839, 500, 2031, 408, -8)?.insetDelta).toBe(-8);
    // Resize or content shrink invalidates a retirement pass. It cannot grow the budget.
    expect(retirementGeometry(1831, 600, 2023, 308)).toBeNull();
    expect(retirementGeometry(1831, 500, 1923, 308)).toBeNull();
  });
});
