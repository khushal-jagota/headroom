<script lang="ts">
  import { PRIORITIES } from "../lib/ui";

  let {
    priority,
    disabled = false,
    surface = "ticket",
    onChange
  }: {
    priority: string;
    disabled?: boolean;
    surface?: "ticket" | "review";
    onChange: (priority: string, select: HTMLSelectElement) => void;
  } = $props();

  let urgent = $derived(priority === "P0" || priority === "P1");
</script>

<span
  class="ticket-identity-fact ticket-identity-priority"
  class:ticket-identity-priority--urgent={urgent}
  data-priority-control={surface === "ticket" ? "" : undefined}
  data-priority-alert={surface === "ticket" && urgent ? priority : undefined}
  data-review-priority-control={surface === "review" ? "" : undefined}
>
  {priority}
  <select
    aria-label="Ticket priority"
    value={priority}
    {disabled}
    onchange={(event) => onChange(event.currentTarget.value, event.currentTarget)}
  >
    {#each PRIORITIES as option}
      <option value={option}>{option}</option>
    {/each}
  </select>
</span>
