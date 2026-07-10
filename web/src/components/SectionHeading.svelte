<script lang="ts">
  let {
    label,
    count,
    variant = "",
    class: extraClass = "",
    ...rest
  }: {
    label: string;
    count?: number;
    variant?: string;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

<div
  class={`section-heading${variant ? ` section-heading--${variant}` : ""}${
    extraClass ? ` ${extraClass}` : ""
  }`}
  {...rest}
>
  <span class="section-heading-label">{label}</span>
  {#if count !== undefined}<span class="section-heading-count">· {count}</span>{/if}
</div>
