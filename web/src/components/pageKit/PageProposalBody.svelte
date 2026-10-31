<script lang="ts">
  import { pageDocumentDigest, prettyPage, readStoredPage, normalizeAuthoredBody } from "../../lib/pageKit";
  import InlineEdit from "../InlineEdit.svelte";
  import PageDocument from "./PageDocument.svelte";

  let { body, storedBody = body, editable = false, onSave }: {
    body: string;
    storedBody?: string;
    editable?: boolean;
    onSave?: (raw: string) => Promise<unknown>;
  } = $props();

  let sourceOpen = $state(false);
  let read = $derived(readStoredPage(body));
  let sourceText = $derived(read.intent === "page" ? prettyPage(read.document) : body);
  let digest = $derived(pageDocumentDigest(storedBody));

  async function saveSource(raw: string): Promise<void> {
    if (!onSave) return;
    await onSave(normalizeAuthoredBody(raw));
  }
</script>

{#if read.intent === "page"}
  <div class="page-mount" data-page-document data-page-digest={digest}>
    <PageDocument document={read.document} />
    {#if editable && onSave}
      <button type="button" class="source-toggle" onclick={() => { sourceOpen = !sourceOpen; }}>
        {sourceOpen ? "Close" : "Edit"}
      </button>
      {#if sourceOpen}
        <div class="source">
          <InlineEdit value={sourceText} multiline dataEdit onSave={saveSource} onCancel={() => sourceText} />
        </div>
      {/if}
    {/if}
  </div>
{:else}
  <div class="page-invalid" data-page-invalid>
    <p>{read.intent === "invalid" ? read.message : "This proposal is not a page."}</p>
    {#if editable && onSave}
      <div class="source">
        <InlineEdit value={body} multiline dataEdit onSave={saveSource} onCancel={() => body} />
      </div>
    {/if}
  </div>
{/if}

<style>
  .page-mount { margin-top: var(--space-4); }
  .source-toggle {
    margin-top: var(--space-5);
    font: inherit;
    font-size: var(--type-sm);
    font-weight: 500;
    color: var(--text-faint);
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
  }
  .source-toggle:hover { color: var(--text-strong); }
  .source { margin-top: var(--space-2); }
  .source :global(.ed) {
    display: block;
    max-height: 24rem;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    line-height: 1.5;
    color: var(--text-default);
  }
  .page-invalid {
    color: var(--accent-error);
    font-size: var(--type-sm);
    line-height: 1.5;
  }
  .page-invalid p { margin: 0; }
</style>
