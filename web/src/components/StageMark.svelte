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

  let waitingCompatibleClass = $derived(
    state === "current-assigned" ? " stage-mark--current-waiting" : ""
  );
</script>

<span
  class={`stage-mark stage-mark--${state}${waitingCompatibleClass}${extraClass ? ` ${extraClass}` : ""}`}
  role="img"
  aria-label={state.replace(/-/g, " ")}
  {...rest}
></span>
