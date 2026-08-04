<script lang="ts">
  import claudeMark from "../../assets/model-picker/claude.svg";
  import codexMark from "../../assets/model-picker/codex.svg";
  import hermesMark from "../../assets/model-picker/hermes.png";
  import type { ConversationBackendKey } from "../../lib/conversation/wire";

  let { backend }: { backend: ConversationBackendKey } = $props();
</script>

<img
  class:hermes={backend === "hermes"}
  class="model-picker-mark"
  src={backend === "claude" ? claudeMark : backend === "codex" ? codexMark : hermesMark}
  alt=""
  aria-hidden="true"
/>

<style>
  .model-picker-mark { display: block; width: 13px; height: 13px; object-fit: contain; }
  .model-picker-mark.hermes { width: 16px; height: 16px; margin: calc(var(--space-1) * -.375); }
</style>
