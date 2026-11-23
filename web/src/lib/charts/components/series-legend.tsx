import React from 'react';

/** A snapshot key uses the same labels and colors as its plot. The colour is a swatch beside
 *  the label, so the words keep the legend's readable tone whatever the series colour is. */
export function SeriesLegend({ series, inset = 0 }: { series: { key: string; label: string; color?: string }[]; inset?: number }) {
  if (series.length < 2) return null;
  // `inset` lines the key up with the plot area's left edge (a chat snapshot's y-axis numbers
  // stand to its left).
  return <ul className="snapshot-legend" aria-label="Chart legend" style={inset > 0 ? { paddingInlineStart: `${inset}px` } : undefined}>{series.map(item =>
    <li key={item.key}><span className="snapshot-legend-swatch" style={{ background: item.color }} aria-hidden="true" />{item.label}</li>
  )}</ul>;
}
