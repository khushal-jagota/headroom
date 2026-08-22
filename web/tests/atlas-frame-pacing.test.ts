// The world runs at the rate it is worth running at.
//
// Full rate for a hand on the canvas and for a journey the eye is making on its own,
// an ambient rate for the drift, and a resting rate once nobody has been there for a
// long while. The one rule that must never break: a slower world is a less smooth
// world and never a slower-moving one, which holds only while the longest step the
// loop will pay out is longer than the slowest interval it asks for.

import { describe, expect, it } from "vitest";

import { frameIntervalSeconds } from "../src/lib/atlas/world/pacing";
import { PACING } from "../src/lib/atlas/world/tuning";

describe("frame pacing", () => {
  it("uses full, ambient, and resting rates at their boundaries", () => {
    expect(frameIntervalSeconds(0, false)).toBe(0);
    expect(frameIntervalSeconds(PACING.fullRateAfterPresence - 0.01, false)).toBe(0);
    expect(frameIntervalSeconds(PACING.idleAfterPresence * 10, true)).toBe(0);
    expect(frameIntervalSeconds(PACING.fullRateAfterPresence, false)).toBeCloseTo(
      1 / PACING.ambientFps
    );
    expect(frameIntervalSeconds(PACING.idleAfterPresence - 0.01, false)).toBeCloseTo(
      1 / PACING.ambientFps
    );
    expect(frameIntervalSeconds(PACING.idleAfterPresence, false)).toBeCloseTo(
      1 / PACING.idleFps
    );
    expect(frameIntervalSeconds(PACING.idleAfterPresence * 10, false)).toBeCloseTo(
      1 / PACING.idleFps
    );
  });

  it("never asks to wait longer than the loop will pay out in one step", () => {
    // The loop clamps dt to maxStepSeconds. A wait longer than the clamp would be
    // time the drift, the fades and the chips never receive, and the resting world
    // would genuinely move slower rather than merely look less smooth.
    const slowest = Math.max(
      frameIntervalSeconds(PACING.idleAfterPresence * 10, false),
      frameIntervalSeconds(PACING.fullRateAfterPresence, false)
    );
    expect(slowest).toBeLessThan(PACING.maxStepSeconds);
  });

  it("only ever slows down as the canvas goes quiet", () => {
    let previous = 0;
    for (let since = 0; since < PACING.idleAfterPresence * 1.5; since += 0.5) {
      const wait = frameIntervalSeconds(since, false);
      expect(wait).toBeGreaterThanOrEqual(previous);
      previous = wait;
    }
  });
});
