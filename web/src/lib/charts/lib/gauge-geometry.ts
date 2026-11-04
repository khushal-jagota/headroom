/** Shared dial geometry. Adapters retain their own value scales and animation. */
const center = 50, radius = 42, start = -135, span = 270;

export function gaugePoint(fraction: number, atRadius: number) {
  const angle = (start + span * fraction) * Math.PI / 180;
  return { x: center + atRadius * Math.sin(angle), y: center - atRadius * Math.cos(angle) };
}

/** Optional precision preserves the read-only dial's original path serialization. */
export function gaugeArc(fraction: number, precision?: 3) {
  const sweep = Math.min(1, Math.max(0, fraction));
  const at = (position: number) => {
    const point = gaugePoint(position, radius);
    return precision === undefined ? `${point.x} ${point.y}` : `${point.x.toFixed(precision)} ${point.y.toFixed(precision)}`;
  };
  return `M ${at(0)} A ${radius} ${radius} 0 ${start + span * sweep - start > 180 ? 1 : 0} 1 ${at(sweep)}`;
}
