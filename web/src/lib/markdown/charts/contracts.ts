import type { ChartBlock, ChartSeries } from '../../goals/contracts';

/** The authored chat subset. Internal identities and goal bindings are never authored. */
export type ChatChartSpec = Pick<ChartBlock, 'title' | 'unit' | 'format' | 'stacked'> & {
  kind: 'bar' | 'line';
  series: Array<Pick<ChartSeries, 'name' | 'points'>>;
};

export type MarkdownChart = { block: ChartBlock; source: string };
