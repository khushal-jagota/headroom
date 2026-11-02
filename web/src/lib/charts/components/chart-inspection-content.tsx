import type { CSSProperties } from 'react';
import styles from './line-chart.module.css';

/** Shared inspection rows. Each chart retains its own interaction and tooltip position. */
export function ChartInspectionContent({ label, rows }: {
  label: string;
  rows: Array<{ key: string; label: string; value: string; color: string; dashed?: boolean }>;
}) {
  return <>
    <p className={styles.tipTitle}>{label}</p>
    {rows.map(row => <p key={row.key} data-tooltip-series={row.key} className={styles.tipRow} style={{ '--series': row.color } as CSSProperties}>
      <span className={styles.tipSwatch} data-dashed={row.dashed || undefined} />
      <span className={styles.tipName}>{row.label}</span>
      <span className={styles.tipValue}>{row.value}</span>
    </p>)}
  </>;
}
