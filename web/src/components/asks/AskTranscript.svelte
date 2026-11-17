<script lang="ts">
  import type { TranscriptLine } from "../../lib/asks/preview";

  let { lines }: { lines: readonly TranscriptLine[] } = $props();
</script>

{#each lines as line, index (`${index}-${line.role}`)}
  {#if line.role === "owner"}
    <article class="chat-u">{line.text}</article>
  {:else if line.role === "note"}
    <p class="chat-system">{line.text}</p>
  {:else}
    <article class="chat-a">{line.text}</article>
  {/if}
{/each}
