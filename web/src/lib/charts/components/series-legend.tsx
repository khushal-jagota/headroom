import React from 'react';

/** A snapshot key uses the same labels and colors as its plot. */
export function SeriesLegend({ series }: { series: { key: string; label: string; color?: string }[] }) {
  if (series.length < 2) return null;
  return <ul className="snapshot-legend" aria-label="Chart legend">{series.map(item =>
    <li key={item.key} style={{ color: item.color }}>{item.label}</li>
  )}</ul>;
}
