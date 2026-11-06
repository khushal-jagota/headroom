<script lang="ts">
  /** The rail's quiet mark for a Ticket that is with an agent rather than with the person.
   *  It stands in the indent before the Ticket's name and says which wait the Ticket is in,
   *  the same thing the group heading said in words. Needs-you groups keep their heading
   *  and carry no mark. */
  let { key, label }: { key: string; label: string } = $props();
</script>
<span class="rail-state-glyph" data-rail-state={key} role="img" aria-label={label} title={label}>
  <svg viewBox="0 0 12 12" aria-hidden="true">
    {#if key === "agent"}
      <!-- At work: a ring three-quarters drawn. -->
      <path d="M6 1.75a4.25 4.25 0 1 1-4.25 4.25" />
    {:else if key === "status_awaiting_approval"}
      <!-- Waiting on an agent's approval: a clock. -->
      <circle cx="6" cy="6" r="4.25" /><path d="M6 3.75V6l1.6 1.1" />
    {:else if key === "waiting_to_closeout"}
      <!-- Waiting on Consequences: a broken ring. -->
      <circle cx="6" cy="6" r="4.25" stroke-dasharray="2.2 1.9" />
    {:else}
      <!-- Nothing under way: a small open ring. -->
      <circle cx="6" cy="6" r="3" />
    {/if}
  </svg>
</span>
<style>
  .rail-state-glyph { display:inline-flex; width:12px; height:12px; color:var(--rail-tone-label); }
  svg { width:100%; height:100%; fill:none; stroke:currentColor; stroke-width:1.3; stroke-linecap:round; stroke-linejoin:round; }
</style>
