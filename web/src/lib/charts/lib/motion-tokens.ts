/** Approved chart timing scales with Panels' central slow-motion token.
 * Numeric Motion/Chart.js durations use seconds; CSS accepts milliseconds too.
 * The fallback keeps non-DOM imports safe and matches the default token value.
 */
function standardSeconds(): number {
  if (typeof document === 'undefined' || typeof getComputedStyle === 'undefined') return .24;
  const value = getComputedStyle(document.documentElement).getPropertyValue('--motion-slow').trim();
  const number = Number.parseFloat(value);
  if (!Number.isFinite(number) || number < 0) return .24;
  return value.endsWith('ms') ? number / 1000 : value.endsWith('s') ? number : .24;
}
export const motionTokens = {
  duration: {
    get instant() { return standardSeconds() / 2; },
    get fast() { return standardSeconds() * 2 / 3; },
    get exit() { return standardSeconds() * 3 / 4; },
    get standard() { return standardSeconds(); },
    get considered() { return standardSeconds() * 2; },
    get bar() { return standardSeconds() * 2.5; },
    get gaugeReveal() { return standardSeconds() * 3.2; },
    get lineReveal() { return standardSeconds() * 3.5; },
    get disclosureDelay() { return standardSeconds() / 3; },
  },
  ease: {
    enter: [0.16, 1, 0.3, 1],
    exit: [0.7, 0, 0.84, 0],
    standard: [0.22, 1, 0.36, 1],
    inOut: [0.65, 0, 0.35, 1],
  },
  spring: {
    responsive: { type: 'spring', stiffness: 520, damping: 44 },
    gentle: { type: 'spring', stiffness: 340, damping: 34 },
    disclosure: { type: 'spring', stiffness: 280, damping: 34, mass: 1 },
    snappy: { type: 'spring', get visualDuration() { return standardSeconds() * 13 / 12; }, bounce: 0 },
    smooth: { type: 'spring', get visualDuration() { return standardSeconds() * 5 / 3; }, bounce: 0 },
    morph: { type: 'spring', get visualDuration() { return standardSeconds() * 7 / 4; }, bounce: 0 },
  },
  stagger: {
    get char() { return standardSeconds() / 15; },
    get word() { return standardSeconds() / 6; },
    get line() { return standardSeconds() / 3; },
    get item() { return standardSeconds() * 7 / 48; },
    get maximum() { return standardSeconds() * 25 / 24; },
  },
  blur: { subtle: 2, soft: 4, text: 8 },
} as const;
