<script lang="ts">
  import { lineDiff } from "../../lib/acp/lineDiff";
  import { panelsFileEditMetadata } from "../../lib/acp/fileEditMetadata";

  let {
    path,
    oldText,
    newText,
    fieldMeta
  }: {
    path: string;
    oldText?: string | null;
    newText: string;
    fieldMeta?: unknown;
  } = $props();

  let bounded = $derived(panelsFileEditMetadata(fieldMeta));
  let rows = $derived(
    bounded?.detailState === "omitted"
      ? []
      : lineDiff(oldText, newText, {
          oldStartLine: bounded?.oldStartLine,
          newStartLine: bounded?.newStartLine,
          emptyNewTextMeansNoLines: bounded?.operation === "delete",
          normalizedSnapshotFragments: bounded !== null,
        }),
  );
  const labels = { context: "Context", delete: "Deleted", add: "Added" } as const;
  const markers = { context: " ", delete: "−", add: "+" } as const;
</script>

<figure class="acp-diff" data-acp-diff>
  <figcaption>{path}</figcaption>
  {#if bounded?.detailState === "truncated"}
    <div class="acp-diff-notice">Some edit detail was truncated</div>
  {:else if bounded?.detailState === "omitted"}
    <div class="acp-diff-notice">Edit detail was omitted</div>
  {/if}
  <div class="acp-diff-lines" role="table" aria-label={`Line changes for ${path}`}>
    {#each rows as row}
      <div class={`acp-diff-line acp-diff-line--${row.kind}`} role="row">
        <span class="acp-diff-number" role="cell">{row.oldLine ?? ""}</span>
        <span class="acp-diff-number" role="cell">{row.newLine ?? ""}</span>
        <span class="acp-diff-marker" aria-label={labels[row.kind]} role="cell">{markers[row.kind]}</span>
        <span role="cell"><code>{row.text || " "}</code></span>
      </div>
    {/each}
  </div>
</figure>

<style>
  .acp-diff { margin: 0; min-width: 0; }
  figcaption {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    margin-bottom: var(--space-1);
  }
  .acp-diff-notice { color: var(--text-faint); font-size: var(--type-xs); margin-bottom: var(--space-1); }
  .acp-diff-lines {
    background: var(--surface-sunken);
    overflow-x: auto;
    padding: var(--space-2);
  }
  .acp-diff-line {
    display: grid;
    grid-template-columns: var(--space-5) var(--space-5) var(--space-4) minmax(0, 1fr);
    min-width: max-content;
  }
  .acp-diff-line--delete { border-left: var(--border-strong) solid var(--accent-error); }
  .acp-diff-line--add { border-left: var(--border-strong) solid var(--accent-done); }
  .acp-diff-number, .acp-diff-marker, code {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre;
  }
  code { color: var(--text-default); }
</style>
