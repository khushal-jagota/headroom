<script lang="ts">
  import type { TicketTroubleNote } from "../lib/types";

  let { notes }: { notes: TicketTroubleNote[] } = $props();

  function recordedTime(unixSeconds: number): string {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(new Date(unixSeconds * 1000));
  }
</script>

{#if notes.length > 0}
  <section class="ticket-trouble-notes" data-ticket-trouble-notes>
    <div class="ticket-trouble-notes-heading">Trouble during the run</div>
    <ol>
      {#each notes as note (note.sequence)}
        <li data-trouble-note={note.sequence}>
          <p>{note.body}</p>
          <time datetime={new Date(note.created_at * 1000).toISOString()}>
            {recordedTime(note.created_at)}
          </time>
        </li>
      {/each}
    </ol>
  </section>
{/if}

<style>
  .ticket-trouble-notes {
    border-bottom: var(--border-hairline) solid var(--border-color);
    padding: var(--space-4) 0;
  }

  .ticket-trouble-notes-heading {
    color: var(--text-muted);
    font-size: var(--type-xs);
    font-weight: 650;
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }

  .ticket-trouble-notes ol {
    display: grid;
    gap: var(--space-3);
    margin: var(--space-3) 0 0;
    padding: 0;
    list-style: none;
  }

  .ticket-trouble-notes li {
    display: grid;
    gap: var(--space-1);
  }

  .ticket-trouble-notes p {
    margin: 0;
    font-family: var(--font-serif);
    font-size: var(--type-serif-md);
  }

  .ticket-trouble-notes time {
    color: var(--text-muted);
    font-size: var(--type-xs);
  }
</style>
