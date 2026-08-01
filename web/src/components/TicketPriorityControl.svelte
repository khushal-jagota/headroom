<script lang="ts">
  import { PRIORITIES } from "../lib/ui";
  import PriorityTile from "./PriorityTile.svelte";

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

</script>

<span
  class="ticket-identity-fact ticket-identity-priority"
  data-priority-control={surface === "ticket" ? "" : undefined}
  data-review-priority-control={surface === "review" ? "" : undefined}
>
  <PriorityTile {priority} decorative />
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
