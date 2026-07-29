<script lang="ts">
  let {
    priority,
    decorative = false,
    class: extraClass = "",
    ...rest
  }: {
    priority: string;
    decorative?: boolean;
    class?: string;
    [key: string]: unknown;
  } = $props();

  let normalizedPriority = $derived(priority.toLowerCase());
</script>

<span
  class={`priority-tile priority-tile--${normalizedPriority}${
    extraClass ? ` ${extraClass}` : ""
  }`}
  data-priority-tile={priority}
  role={decorative ? undefined : "img"}
  aria-label={decorative ? undefined : `Priority ${priority}`}
  aria-hidden={decorative ? "true" : undefined}
  {...rest}
>{priority}</span>
