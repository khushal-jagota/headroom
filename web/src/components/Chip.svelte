<script lang="ts">
  import { markerLabel } from "../lib/ui";

  let {
    variant = "",
    value = "",
    keyLabel = "",
    overdue = false
  }: { variant?: string | null; value?: unknown; keyLabel?: string; overdue?: boolean } = $props();

  const markerVariants = new Set([
    "pending-proposal",
    "blockers-cleared",
    "frozen",
    "agent",
    "errored",
    "user",
    "paired"
  ]);

  let classes = $derived.by(() => {
    const out = ["chip"];
    if (variant === "priority") {
      out.push("chip--priority", `chip--${String(value).toLowerCase()}`);
    } else if (variant === "state") {
      out.push("chip--state");
    } else if (variant === "project") {
      out.push("chip--project");
    } else if (variant === "deadline") {
      out.push("chip--deadline");
      if (overdue) out.push("chip--overdue");
    } else if (variant === "blocked-by") {
      out.push("chip--blocked-by");
    } else if (variant && markerVariants.has(variant)) {
      out.push(`chip--${variant}`);
    }
    return out.join(" ");
  });

  let label = $derived.by(() => {
    if (variant && markerVariants.has(variant)) return markerLabel(variant);
    if (value === null || value === undefined) return "";
    if (variant === "blocked-by") return String(value);
    return String(value).replace(/_/g, " ");
  });
</script>

<span
  class={classes}
  data-value={variant === "state" ? String(value) : undefined}
>{#if keyLabel}<span class="k">{keyLabel}</span>{/if}{label}</span>
