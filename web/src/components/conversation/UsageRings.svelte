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

  function dash(percent: number, radius: number): string {
    const total = circumference(radius);
    const used = total * Math.min(100, Math.max(0, percent)) / 100;
    return `${used} ${total - used}`;
  }

  function ringLabel(label: string, window: BackendUsageWindow | null): string {
    if (window === null) return `${label}: nothing reported`;
    const reset = new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(new Date(window.resets_at));
    const warning = usageIsNearlySpent(window.used_percent) ? ", warning, nearly spent" : "";
    return `${label}: ${window.used_percent}% used${warning}, resets ${reset}`;
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
    >
      <svg
        width={compact ? 18 : 30}
        height={compact ? 18 : 30}
        viewBox={`0 0 ${compact ? 18 : 30} ${compact ? 18 : 30}`}
        aria-hidden="true"
      >
        <circle
          cx={compact ? 9 : 15}
          cy={compact ? 9 : 15}
          r={compact ? 7 : 12}
          fill="none"
          stroke="var(--border-color)"
          stroke-width={compact ? 2 : 3}
          stroke-dasharray={value.window === null ? "2 3" : undefined}
        />
        {#if value.window !== null}
          <circle
            cx={compact ? 9 : 15}
            cy={compact ? 9 : 15}
            r={compact ? 7 : 12}
            fill="none"
            stroke={usageIsNearlySpent(value.window.used_percent) ? "var(--accent-error)" : "var(--text-faint)"}
            stroke-width={compact ? 2 : 3}
            stroke-dasharray={dash(value.window.used_percent, compact ? 7 : 12)}
            transform={`rotate(-90 ${compact ? 9 : 15} ${compact ? 9 : 15})`}
          />
        {/if}
      </svg>
      {#if !compact}
        <span class="usage-ring-percent" aria-hidden="true">{value.window === null ? "—" : `${value.window.used_percent}%`}</span>
      {/if}
    </span>
  {/each}
</span>

<style>
  .usage-rings { display: inline-flex; gap: var(--space-3); }
  .usage-rings.compact { gap: var(--space-2); }
  .usage-ring { display: grid; justify-items: center; gap: var(--border-hairline); }
  .usage-ring svg { display: block; }
  .usage-ring-percent {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
  }
  .usage-ring.absent .usage-ring-percent { color: var(--text-faintest); }
  .usage-ring.spent .usage-ring-percent { color: var(--accent-error); }
</style>
