<script lang="ts">
  import type { Snippet } from "svelte";
  import type { FieldStageVisualState } from "../lib/ui";

  let {
    stageState,
    name,
    dataField = name,
    defaultOpen = false,
    children
  }: {
    stageState: FieldStageVisualState;
    name: string;
    dataField?: string;
    defaultOpen?: boolean;
    children?: Snippet;
  } = $props();
</script>

<details class="fsec" data-field={dataField} data-stage-state={stageState} open={defaultOpen}>
  <summary>
    <span
      class={`fsec-mark fsec-mark--${stageState}`}
      role="img"
      aria-label={stageState.replace(/-/g, " ")}
    ></span>
    <span class="fsec-name">{name}</span>
    <span class="fsec-chev"></span>
  </summary>
  <div class="fsec-body">
    {@render children?.()}
  </div>
</details>
