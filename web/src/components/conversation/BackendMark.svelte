<script lang="ts">
  import type { BackendMarkPresentation } from "../../lib/drivers/presentation";

  /* A toned mark is drawn in the text colour around it (the composer's run chip, where the
   * mark is small print beside the model's name); otherwise the provider's own colours.
   * A picture (a full-colour icon with its own margin) is drawn slightly larger. */
  let { mark, toned = false }: { mark: BackendMarkPresentation; toned?: boolean } = $props();
</script>

{#if mark.kind === "icon"}
  {#if toned}
    <span
      class:picture={mark.picture}
      class="model-picker-mark toned"
      style:--model-picker-mark-source={`url("${mark.url}")`}
      aria-hidden="true"
    ></span>
  {:else}
    <img
      class:picture={mark.picture}
      class="model-picker-mark"
      src={mark.url}
      alt=""
      aria-hidden="true"
    />
  {/if}
{:else if mark.kind === "initials"}
  <span class:small={toned} class="model-picker-mark initials" aria-hidden="true">{mark.text}</span>
{:else}
  <span class:small={toned} class="model-picker-mark" aria-hidden="true"></span>
{/if}

<style>
  .model-picker-mark { display: block; width: 13px; height: 13px; object-fit: contain; }
  .model-picker-mark.picture { width: 16px; height: 16px; margin: calc(var(--space-1) * -.375); }
  .model-picker-mark.toned { flex: none; background-color: currentColor; mask: var(--model-picker-mark-source) center / contain no-repeat; }
  .model-picker-mark.toned:not(.picture) { width: var(--composer-mark); height: var(--composer-mark); }
  /* A backend the driver list does not name: its initials, in the same box. */
  .model-picker-mark.small { flex: none; width: var(--composer-mark); height: var(--composer-mark); }
  .model-picker-mark.initials {
    display: grid; place-items: center; flex: none; color: currentColor; background: var(--surface-recessed);
    border-radius: var(--radius-sm); font-size: calc(var(--composer-mark) * .62); line-height: 1;
  }
</style>
