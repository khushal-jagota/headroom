<script lang="ts">
  /** One managed file on its own address.
   *
   * The way into a file from outside a Ticket screen — a shared link, a notification, a
   * link on another screen. A file clicked inside a Ticket opens there instead, on the
   * page it was clicked from; both draw the same document through `FileDocument`.
   */
  import FileDocument from "../components/FileDocument.svelte";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import { resolvePreview, targetFromPreviewQuery } from "../lib/filePreview";

  function parseTarget(): ManagedFileTarget | null {
    const hash = window.location.hash;
    const queryIndex = hash.indexOf("?");
    return targetFromPreviewQuery(
      new URLSearchParams(queryIndex >= 0 ? hash.slice(queryIndex + 1) : "")
    );
  }

  let target = $derived(parseTarget());
  let kind = $derived(target ? resolvePreview(target).kind : null);
  let isDocumentPreview = $derived(kind === "html" || kind === "markdown");
</script>

<section
  class={`file-preview-route${isDocumentPreview ? " file-preview-route--document" : ""}`}
  data-file-preview-route
>
  {#if target}
    <FileDocument {target} />
  {:else}
    <div class="quiet-line">file preview not found</div>
  {/if}
</section>
