<script lang="ts">
  /** The backend's own words behind a status's Details: kept whole and selectable, with a
   *  copy button. Open from the start when they are the only explanation there is. */
  let { id, detail, open }: { id: string; detail: string; open: boolean } = $props();
  let copied = $state(false);
  async function copyDetails(): Promise<void> {
    try {
      await navigator.clipboard.writeText(detail);
      copied = true;
      setTimeout(() => (copied = false), 1_500);
    } catch {
      copied = false;
    }
  }
</script>

<div class="c2-backend-words" {id} hidden={!open}>
  <span class="c2-backend-words-text" data-conversation-status-detail>{detail}</span>
  <button type="button" class="cv-icon-button cv-icon-button--small c2-backend-words-copy" data-conversation-status-copy
    aria-label={copied ? "Details copied" : "Copy details"} title={copied ? "Copied" : "Copy details"} onclick={() => void copyDetails()}>
    <svg viewBox="0 0 16 16" aria-hidden="true">
      {#if copied}<path d="m3.5 8.5 3 3 6-7" />
      {:else}<rect x="5.5" y="5.5" width="8" height="8" rx="1.75" /><path d="M10.5 3.5v-.25A1.75 1.75 0 0 0 8.75 1.5h-5.5A1.75 1.75 0 0 0 1.5 3.25v5.5A1.75 1.75 0 0 0 3.25 10.5h.25" />{/if}
    </svg>
  </button>
</div>

<style>
  /* A code block (#1a1a1a, a 1px #2a2a2a edge, radius 12, 13/20 mono), 8 under its line. */
  .c2-backend-words { position: relative; min-width: 0; margin-block-start: var(--cv-s2); text-align: start; }
  .c2-backend-words[hidden] { display: none; }
  .c2-backend-words-text {
    display: block;
    padding: 12px 48px 12px 16px;
    border: 1px solid var(--cv-surface-edge, #2a2a2a);
    border-radius: 12px;
    background: var(--cv-code-surface, #1a1a1a);
    color: var(--cv-detail-ink, #b0b0b0);
    font-family: var(--cv-mono);
    font-size: var(--cv-type-code);
    line-height: var(--cv-leading-code);
    white-space: pre-wrap;
    user-select: text;
  }
  .c2-backend-words-copy { position: absolute; top: 4px; right: 4px; }
</style>
