import {afterEach, expect, it, vi} from 'vitest';
import {restorationWait} from '../src/components/conversation/viewport/restorationWait';
import {TEXT_RESTORATION_WAIT_MS} from '../src/components/conversation/viewport/viewportConfiguration';

afterEach(() => vi.useRealTimers());

it('bounds active time even without layout progress and pauses the remaining budget off screen', () => {
  vi.useFakeTimers();
  const expire = vi.fn();
  const wait = restorationWait(TEXT_RESTORATION_WAIT_MS, expire);
  wait.active(true);
  vi.advanceTimersByTime(2_000);
  wait.active(false);
  expect(vi.getTimerCount()).toBe(0);
  vi.advanceTimersByTime(60_000);
  expect(expire).not.toHaveBeenCalled();
  wait.active(true);
  // Repeated layout notifications do not restart an attempt's budget.
  wait.active(true);
  vi.advanceTimersByTime(2_999);
  expect(expire).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(expire).toHaveBeenCalledOnce();
  wait.active(true);
  expect(vi.getTimerCount()).toBe(0);
});

it('retires completed or canceled attempts and gives Retry an independent full budget', () => {
  vi.useFakeTimers();
  const stale = vi.fn(), current = vi.fn();
  const original = restorationWait(TEXT_RESTORATION_WAIT_MS, stale);
  original.active(true);
  vi.advanceTimersByTime(4_000);
  original.stop();
  original.active(true);
  expect(vi.getTimerCount()).toBe(0);
  const retry = restorationWait(TEXT_RESTORATION_WAIT_MS, current);
  retry.active(true);
  vi.advanceTimersByTime(4_999);
  expect(stale).not.toHaveBeenCalled();
  expect(current).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(current).toHaveBeenCalledOnce();
  retry.stop();
  expect(vi.getTimerCount()).toBe(0);
});
