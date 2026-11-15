/** Adapter timings that are not part of the unchanged approved counter presets. */
import { motionTokens as approvedMotion } from './motion-tokens';

export const motionTokens = {
  ...approvedMotion,
  duration: {
    ...approvedMotion.duration,
    bar: .6,
    gaugeReveal: .768,
    lineReveal: .84,
    disclosureDelay: .08,
  },
  spring: {
    ...approvedMotion.spring,
    disclosure: { type: 'spring', stiffness: 280, damping: 34, mass: 1 },
  },
} as const;
