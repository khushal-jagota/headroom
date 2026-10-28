<script lang="ts">
  import InlineEdit from '../InlineEdit.svelte';

  let { value, revision, multiline = false, ariaLabel, onSave }: {
    value: string;
    revision: number;
    multiline?: boolean;
    ariaLabel: string;
    onSave: (value: string, revision: number) => Promise<unknown>;
  } = $props();

  // A remote refresh changes the props, but never the base of an open draft.
  // InlineEdit retains failed drafts. Retry keeps this base; Escape and a new
  // focus start a fresh edit against the current document.
  let baseRevision: number | null = null;
  function start() { baseRevision = revision; }
  function save(raw: string) {
    if (baseRevision === null) return Promise.reject(new Error('Start the edit before saving'));
    return onSave(raw, baseRevision);
  }
</script>

<InlineEdit {value} {multiline} {ariaLabel} onStart={start} onSave={save}/>
