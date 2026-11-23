<script lang="ts">
  /** The numbered key and words shared by permission and question answers.
   * The parent keeps its button, answer state, and keyboard behavior. */
  let { label, description = null, digit = null, selected = false, keyTag = "span" }: {
    label: string;
    description?: string | null;
    digit?: number | null;
    selected?: boolean;
    keyTag?: "kbd" | "span";
  } = $props();
</script>

<svelte:element this={keyTag} class="cv-ask-key" aria-hidden="true">
  {#if selected}<svg viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7" /></svg>{:else}{digit ?? ""}{/if}
</svelte:element>
<span class="cv-ask-words">
  <span class="cv-ask-label">{label}</span>
  {#if description}<small class="cv-ask-sub">{description}</small>{/if}
</span>
