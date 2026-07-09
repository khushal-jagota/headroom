<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    title,
    defaultOpen = true,
    tone = "default",
    section = "",
    children
  }: {
    title: string;
    defaultOpen?: boolean;
    tone?: "default" | "support";
    section?: string;
    children?: Snippet;
  } = $props();
</script>

<details
  class="content-disclosure content-disclosure--{tone}"
  data-content-section={section || undefined}
  open={defaultOpen}
>
  <summary class="content-disclosure-summary">
    <span class="content-disclosure-title">{title}</span>
    <span class="content-disclosure-chev" aria-hidden="true"></span>
  </summary>
  <div class="content-disclosure-body">
    {@render children?.()}
  </div>
</details>
