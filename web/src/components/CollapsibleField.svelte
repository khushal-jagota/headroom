<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    mark,
    name,
    dataField = name,
    children
  }: { mark: string; name: string; dataField?: string; children?: Snippet } = $props();

  const kindByMark: Record<string, string> = {
    "✓": "done",
    "●": "now",
    "○": "todo"
  };
  let kind = $derived(kindByMark[mark] || "");
</script>

<details class="fsec" data-field={dataField}>
  <summary>
    <span class={`fsec-mark${kind ? ` fsec-mark--${kind}` : ""}`}>{mark}</span>
    <span class="fsec-name">{name}</span>
    <span class="fsec-chev"></span>
  </summary>
  <div class="fsec-body">
    {@render children?.()}
  </div>
</details>
