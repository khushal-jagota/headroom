import { describe, expect, it } from "vitest";

import { createCoalescedAsyncCall } from "../src/lib/coalescedAsyncCall";

describe("coalesced async calls", () => {
  it("coalesces duplicate requests into one trailing pass", async () => {
    const passes: boolean[] = [];
    let finish = (): void => {
      throw new Error("the pass did not start");
    };
    const request = createCoalescedAsyncCall(async (force) => {
      passes.push(force);
      if (passes.length === 1) {
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
      }
    });

    const first = request();
    const duplicate = request();
    expect(passes).toEqual([false]);

    finish();
    await Promise.all([first, duplicate]);
    expect(passes).toEqual([false, false]);
  });

  it("hands settlement-gap intent to a trailing owner", async () => {
    const passes: boolean[] = [];
    let settleFirst = (): void => {
      throw new Error("the first pass did not start");
    };
    let lateRequest: Promise<void> | null = null;
    let request: (force?: boolean) => Promise<void>;

    request = createCoalescedAsyncCall(async (force) => {
      passes.push(force);
      if (passes.length === 1) {
        await new Promise<void>((resolve) => {
          settleFirst = () => {
            resolve();
            // The drain continuation is already queued. This microtask therefore calls
            // request after the drain observes no pending work but before its `finally`
            // detaches the owner — the settlement gap this contract must close.
            queueMicrotask(() => {
              lateRequest = request(true);
            });
          };
        });
      }
    });

    const firstRequest = request();
    settleFirst();

    await firstRequest;
    await lateRequest;

    expect(passes).toEqual([false, true]);
  });
});
