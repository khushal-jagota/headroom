<script lang="ts">
  import type { FieldStageVisualState } from "../lib/ui";

  let {
    state,
    class: extraClass = "",
    ...rest
  }: {
    state: FieldStageVisualState;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

<span
  class={`stage-mark stage-mark--${state}${extraClass ? ` ${extraClass}` : ""}`}
  role="img"
  aria-label={state.replace(/-/g, " ")}
  {...rest}
></span>
