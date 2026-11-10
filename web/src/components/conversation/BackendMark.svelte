<script lang="ts">
  import claudeMark from "../../assets/model-picker/claude.svg";
  import codexMark from "../../assets/model-picker/codex.svg";
  import hermesMark from "../../assets/model-picker/hermes.png";
  import type { ConversationBackendKey } from "../../lib/conversation/wire";

  /* A toned mark is drawn in the text colour around it (the composer's run chip, where the
   * mark is small print beside the model's name); otherwise the provider's own colours. */
  let { backend, toned = false }: { backend: ConversationBackendKey; toned?: boolean } = $props();
  let source = $derived(backend === "claude" ? claudeMark : backend === "codex" ? codexMark : hermesMark);
</script>

{#if toned}
  <span
    class:hermes={backend === "hermes"}
    class="model-picker-mark toned"
    style:--model-picker-mark-source={`url("${source}")`}
    aria-hidden="true"
  ></span>
{:else}
  <img
    class:hermes={backend === "hermes"}
    class="model-picker-mark"
    src={source}
    alt=""
    aria-hidden="true"
  />
{/if}

<style>
  .model-picker-mark { display: block; width: 13px; height: 13px; object-fit: contain; }
  .model-picker-mark.hermes { width: 16px; height: 16px; margin: calc(var(--space-1) * -.375); }
  .model-picker-mark.toned { flex: none; background-color: currentColor; mask: var(--model-picker-mark-source) center / contain no-repeat; }
  .model-picker-mark.toned:not(.hermes) { width: var(--composer-mark); height: var(--composer-mark); }
</style>
