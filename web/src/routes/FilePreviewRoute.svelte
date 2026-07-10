<script lang="ts">
  import FilePreview from "../components/FilePreview.svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import { chatFileTarget, ticketFileTarget } from "../lib/filePreview";

  function parseTarget(): FilePreviewTarget | null {
    const hash = window.location.hash;
    const queryIndex = hash.indexOf("?");
    const params = new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : "");
    const path = params.get("path") || "";
    if (params.get("source") === "ticket") {
      return ticketFileTarget(params.get("ticket") || "", path);
    }
    if (params.get("source") === "chat") {
      return chatFileTarget(params.get("entity") || "", path);
    }
    return null;
  }

  let target = $derived(parseTarget());
</script>

<section class="file-preview-route" data-file-preview-route>
  {#if target}
    <FilePreview {target} mode="full" />
  {:else}
    <div class="quiet-line">file preview not found</div>
  {/if}
</section>
