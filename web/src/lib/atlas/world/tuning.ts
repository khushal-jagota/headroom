// The numbers that decide how the place feels.
//
// Sizes, distances, timings and thresholds that were arrived at by looking at the
// world and adjusting until it read right. They live here so they can be adjusted
// again without reading the code that spends them. Numbers that are inseparable
// from one drawing — the proportions of a temple, the swing of a mason's arm —
// stay with that drawing.

// ---------------- the sky through the day ----------------

export type DayKeyframe = {
  h: number;
  bg: string;
  fog: string;
  sun: number;
  sunC: string;
  hemi: number;
  stars: number;
};

export const DAY_KEYS: DayKeyframe[] = [
  { h: 0.0, bg: "#0b1022", fog: "#141a2e", sun: 0.0, sunC: "#a9c0e8", hemi: 0.22, stars: 1.0 },
  { h: 5.0, bg: "#101830", fog: "#1a2338", sun: 0.0, sunC: "#a9c0e8", hemi: 0.24, stars: 0.9 },
  { h: 6.2, bg: "#5d6b8a", fog: "#b98f7d", sun: 0.9, sunC: "#ffd2a0", hemi: 0.5, stars: 0.15 },
  { h: 8.0, bg: "#84b0cf", fog: "#e3d6bd", sun: 1.9, sunC: "#ffe9cc", hemi: 0.85, stars: 0.0 },
  { h: 13.0, bg: "#8fc3de", fog: "#e9f2ee", sun: 2.6, sunC: "#fff6e6", hemi: 1.0, stars: 0.0 },
  { h: 17.5, bg: "#87b1cd", fog: "#e6d4ba", sun: 2.0, sunC: "#ffe4bc", hemi: 0.85, stars: 0.0 },
  { h: 19.6, bg: "#5d6b8a", fog: "#c98f6f", sun: 0.8, sunC: "#ffb87e", hemi: 0.5, stars: 0.1 },
  { h: 20.8, bg: "#232c48", fog: "#3c4460", sun: 0.0, sunC: "#a9c0e8", hemi: 0.3, stars: 0.6 },
  { h: 22.0, bg: "#0e1428", fog: "#161d32", sun: 0.0, sunC: "#a9c0e8", hemi: 0.24, stars: 1.0 },
  { h: 24.0, bg: "#0b1022", fog: "#141a2e", sun: 0.0, sunC: "#a9c0e8", hemi: 0.22, stars: 1.0 }
];

export const SKY = {
  // how far out the sun and moon ride, and how high at their peak
  orbitRadius: 60,
  sunHeight: 44,
  sunBase: 2,
  moonHeight: 38,
  moonBase: 4,
  moonIntensity: 0.5,
  // the discs are drawn further out than the lights that cast from them
  sunDiscDistance: 3.4,
  moonDiscDistance: 3.2,
  starCount: 900,
  starRadius: 300,
  starSeed: 1042
};

// ---------------- water and haze ----------------

export const SEA = {
  radius: 260,
  y: -0.9
};

export const FOG = {
  // haze begins beyond the home framing, wherever that is for this world
  minNear: 75,
  nearFraction: 0.95,
  depth: 175
};

// ---------------- camera, travel, and the idle drift ----------------

export const CAMERA = {
  fov: 42,
  near: 0.1,
  far: 500,
  start: { x: 14, y: 10, z: 12 },
  targetY: 0.8,
  minDistance: 3.2,
  maxDistance: 140,
  minPolarAngle: 0.12,
  maxPolarAngle: 1.42,
  dampingFactor: 0.06,
  // framing the whole archipelago
  frameAzimuth: 0.62,
  framePolar: 1.02,
  framePadding: 2,
  frameMin: 16,
  frameMax: 130,
  // a journey eases both where we look and how close we stand
  flightEase: 2.4,
  arriveTargetDistance: 0.1,
  arriveRangeDistance: 0.25,
  // the drift resumes a few breaths after arriving, later after a manual drag
  idleAfterInteraction: 22,
  idleAfterArrival: 4,
  // the drift reads clearly however close we stand
  autoRotateSlow: 0.5,
  autoRotateGain: 0.7,
  autoRotateNearDistance: 55,
  autoRotateSpan: 45
};

// how close each kind of thing is read from
export const ARRIVAL = {
  itemLift: 2.4,
  itemPadFactor: 3.0,
  overseerLift: 2.0,
  overseerDistance: 9,
  figureLift: 0.7,
  figureDistance: 6.5,
  islandLift: 1.6,
  islandRadiusFactor: 2.0
};

// ---------------- the archipelago's layout ----------------

export const LAYOUT = {
  // sprint items stand well apart; the island grows to carry the spacing
  padMin: 6.4,
  padBase: 5.0,
  padPerLiveTicket: 0.5,
  padPerTicketCap: 2.0,
  padPerTicket: 0.14,
  // the clear ground kept between one pad and the next
  padGap: 5,
  // the ring the Items present at first sight are set out on. An island drawn for
  // the first time looks exactly as it always has; only Items that arrive later
  // are placed by the outward search.
  ringTwo: 5,
  ringSpread: 2.05,
  ringPerItem: 1.5,
  shoreMargin: 3.5,
  // islands are large and far apart, so no project can be lost
  islandGap: 22,
  islandStagger: 7,
  islandStaggerFraction: 0.6,
  // the settled building size; separation comes from spacing, not scale
  buildScale: 2.1,
  buildPadMargin: 1.4
};

// ---------------- what resolves at what range ----------------

export const LOD = {
  // from afar the overseers hold the scene; come closer and the crew resolves
  nearRadiusFactor: 2.1,
  nearMargin: 14,
  // hysteresis, so a figure at the threshold does not flicker in and out
  hysteresis: 6,
  fadeRate: 6,
  hideBelow: 0.03,
  // the island's name surfaces when you are far enough to need it
  nameFrom: 4,
  nameSpan: 11,
  nameMaxOpacity: 0.95,
  nameFadeRate: 4,
  nameScaleRange: 30
};

// ---------------- speech bubbles ----------------

export const BUBBLE = {
  life: 12,
  fadeOut: 1.8,
  fadeIn: 0.35,
  floatAmplitude: 0.03,
  floatRate: 1.4,
  // readable from any distance: they hold their apparent size as you pull back
  rangeUnit: 17,
  rangeMax: 2.6,
  workerLift: 1.5,
  steleLift: 0.9
};

// ---------------- stone chips ----------------

export const CHIPS = {
  max: 40,
  life: 0.65,
  gravity: 6,
  rise: 1.6,
  spread: 1.4
};

// ---------------- selection ring ----------------

export const RING = {
  inner: 0.4,
  outer: 0.52,
  pulseRate: 2.5,
  pulseBase: 0.55,
  pulseSwing: 0.2
};

// ---------------- picking ----------------

export const TAP = {
  // a tap, not a drag: it moved less than this and was let go before this
  moveTolerance: 8,
  holdMs: 600,
  hoverThrottleMs: 120
};
