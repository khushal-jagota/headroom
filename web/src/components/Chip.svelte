<script lang="ts">
  let {
    variant = "",
    value = "",
    keyLabel = ""
  }: { variant?: string | null; value?: unknown; keyLabel?: string } = $props();

  let classes = $derived(variant === "project" ? "chip chip--project" : "chip");

  let label = $derived.by(() => {
    if (value === null || value === undefined) return "";
    return String(value).replace(/_/g, " ");
  });
</script>

<span class={classes}>{#if keyLabel}<span class="k">{keyLabel}</span>{/if}{label}</span>
