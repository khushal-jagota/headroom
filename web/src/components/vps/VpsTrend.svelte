<script lang="ts">
  import type { Trend, TrendPoint } from '../../lib/vpsMetrics/contracts';
  let { trend, ceiling = null }: { trend: Trend; ceiling?: number | null } = $props();
  const width = 960, height = 160;
  let maximum = $derived(ceiling ?? Math.max(1, ...[...trend.primary, ...trend.secondary].map(point => point.value ?? 0)) * 1.15);
  function segments(points: TrendPoint[]): { line: string; area: string; x: number; y: number }[] {
    const groups: TrendPoint[][] = [];
    let current: TrendPoint[] = [];
    for (const point of points) {
      const previous = current.at(-1);
      if (point.value === null || (previous && (point.boot_id !== previous.boot_id || point.timestamp - previous.timestamp > trend.gapSeconds))) {
        if (current.length) groups.push(current);
        current = [];
      }
      if (point.value !== null) current.push(point);
    }
    if (current.length) groups.push(current);
    return groups.map(group => {
      const coordinates = group.map(point => ({
        x: Math.max(0, Math.min(width, (point.timestamp - trend.start) / (trend.end - trend.start) * width)),
        y: height - Math.max(0, Math.min(1, (point.value ?? 0) / maximum)) * height
      }));
      const first = coordinates[0], last = coordinates[coordinates.length - 1];
      const line = coordinates.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
      return { line, area: `M${first.x},${height} ${line.replace(/^M/, 'L')} L${last.x},${height} Z`, ...last };
    });
  }
  let primary = $derived(segments(trend.primary));
  let secondary = $derived(segments(trend.secondary));
</script>

<div class="trend" aria-hidden="true">
  <svg viewBox="0 0 960 160" preserveAspectRatio="none">
    {#each primary as segment}
      <path class="trend-area" d={segment.area} />
      <path class="trend-line" d={segment.line} />
    {/each}
    {#each secondary as segment}<path class="trend-line trend-line--secondary" d={segment.line} />{/each}
    {#if primary.length}<circle class="trend-now" cx={primary[primary.length - 1].x} cy={primary[primary.length - 1].y} r="5" />{/if}
  </svg>
</div>
