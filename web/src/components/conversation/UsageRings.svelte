<script lang="ts">
  import type { BackendUsageWindow } from "../../lib/conversation/wire";
  import { usageIsNearlySpent, usageWindow } from "../../lib/conversation/usagePresentation";

  let {
    windows,
    modelId = null,
    label = "Usage allowances",
    compact = false
  }: {
    windows: readonly BackendUsageWindow[];
    modelId?: string | null;
    label?: string;
    compact?: boolean;
  } = $props();

  let fiveHour = $derived(usageWindow(windows, "five_hour", modelId));
  let week = $derived(usageWindow(windows, "seven_day", modelId));
  let values = $derived([
    { kind: "five_hour", label: "5 hr", window: fiveHour },
    { kind: "seven_day", label: "week", window: week }
  ]);

  function circumference(radius: number): number {
    return 2 * Math.PI * radius;
  }

  function remainingPercent(usedPercent: number): number {
    return 100 - Math.min(100, Math.max(0, usedPercent));
  }

  function dash(remainingPercent: number, radius: number): string {
    const total = circumference(radius);
    const remaining = total * remainingPercent / 100;
    return `${remaining} ${total - remaining}`;
  }

  function ringLabel(label: string, window: BackendUsageWindow | null): string {
    if (window === null) return `${label}: nothing reported`;
    const reset = new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(new Date(window.resets_at));
    const warning = usageIsNearlySpent(window.used_percent) ? ", warning, nearly spent" : "";
    return `${label}: ${remainingPercent(window.used_percent)}% remaining${warning}, resets ${reset}`;
  }
</script>

<span class="usage-rings" class:compact role="group" aria-label={label}>
  {#each values as value (value.label)}
    <span
      class="usage-ring"
      class:absent={value.window === null}
      class:spent={value.window !== null && usageIsNearlySpent(value.window.used_percent)}
      title={ringLabel(value.label, value.window)}
      aria-label={ringLabel(value.label, value.window)}
      data-usage-window={value.kind}
      data-usage-percent={value.window?.used_percent}
      data-remaining-percent={value.window === null ? undefined : remainingPercent(value.window.used_percent)}
    >
      <span class="usage-ring-visual">
        <svg
          width={compact ? 18 : 34}
          height={compact ? 18 : 34}
          viewBox={`0 0 ${compact ? 18 : 34} ${compact ? 18 : 34}`}
          aria-hidden="true"
        >
          <circle
            cx={compact ? 9 : 17}
            cy={compact ? 9 : 17}
            r={compact ? 7 : 14}
            fill="none"
            stroke="var(--border-color)"
            stroke-width={compact ? 2 : 2.5}
            stroke-dasharray={value.window === null ? "2 3" : undefined}
          />
          {#if value.window !== null}
            <circle
              cx={compact ? 9 : 17}
              cy={compact ? 9 : 17}
              r={compact ? 7 : 14}
              fill="none"
              stroke={usageIsNearlySpent(value.window.used_percent) ? "var(--accent-error)" : "var(--text-faint)"}
              stroke-width={compact ? 2 : 2.5}
              stroke-dasharray={dash(remainingPercent(value.window.used_percent), compact ? 7 : 14)}
              transform={`rotate(-90 ${compact ? 9 : 17} ${compact ? 9 : 17})`}
            />
          {/if}
        </svg>
        {#if !compact}
          <span class="usage-ring-percent" aria-hidden="true">{value.window === null ? "—" : remainingPercent(value.window.used_percent)}</span>
        {/if}
      </span>
      {#if !compact}<span class="usage-ring-label" aria-hidden="true">{value.label}</span>{/if}
    </span>
  {/each}
</span>

<style>
  .usage-rings { display: inline-flex; gap: var(--space-2); }
  .usage-rings.compact { gap: var(--space-2); }
  .usage-ring { display: grid; justify-items: center; gap: var(--space-1); }
  .usage-ring-visual { display: grid; place-items: center; }
  .usage-ring-visual > * { grid-area: 1 / 1; }
  .usage-ring svg { display: block; }
  .usage-ring-percent {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    line-height: 1;
  }
  .usage-ring-label { color: var(--text-faintest); font-family: var(--font-mono); font-size: var(--type-xs); line-height: 1; }
  .usage-ring.absent .usage-ring-percent { color: var(--text-faintest); }
  .usage-ring.spent .usage-ring-percent { color: var(--accent-error); }
</style>
