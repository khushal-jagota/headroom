import React from 'react';

/** A snapshot key uses the same labels and colors as its plot. The colour is a swatch beside
 *  the label, so the words keep the legend's readable tone whatever the series colour is. */
export function SeriesLegend({ series }: { series: { key: string; label: string; color?: string }[] }) {
  if (series.length < 2) return null;
  return <ul className="snapshot-legend" aria-label="Chart legend">{series.map(item =>
    <li key={item.key}><span className="snapshot-legend-swatch" style={{ background: item.color }} aria-hidden="true" />{item.label}</li>
  )}</ul>;
}
