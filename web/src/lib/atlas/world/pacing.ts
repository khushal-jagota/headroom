// How often the world is worth stepping.
//
// Atlas costs the same whether or not anybody is there, because the loop takes every
// frame the browser offers it. This is the one decision that changes that: given how
// long it has been since a person was on the canvas, and whether the camera is
// travelling of its own accord, how long to wait before stepping the world again.
//
// Waiting is safe here because nothing in the world measures itself in frames. The
// ambient motions are all sin(absolute time), and everything that accumulates — the
// drift, the fades, the chips, a flight — is multiplied by dt. A slower world is a
// less smooth world and never a slower-moving one.

import { PACING } from "./tuning";

// Zero means take every frame the browser offers.
export function frameIntervalSeconds(
  secondsSincePresence: number,
  travelling: boolean
): number {
  if (travelling || secondsSincePresence < PACING.fullRateAfterPresence) return 0;
  if (secondsSincePresence >= PACING.idleAfterPresence) return 1 / PACING.idleFps;
  return 1 / PACING.ambientFps;
}
