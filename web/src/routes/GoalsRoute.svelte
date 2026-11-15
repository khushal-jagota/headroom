<script lang="ts">
  /** All goals, as its own destination in the main navigation. */
  import { createQuery } from "@tanstack/svelte-query";
  import { goalQueries } from "../lib/goals/queries";
  import { refreshAtBoundary } from "../lib/goals/presentation";
  import ErrorLine from "../components/ErrorLine.svelte";
  import GoalsOverview from "../components/goals/GoalsOverview.svelte";

  const goals = createQuery(() => goalQueries.list());
  $effect(() => { if (goals.data) return refreshAtBoundary(goals.data.next_refresh_at, () => void goals.refetch()); });
</script>

<section class="all-goals-page">
{#if goals.error}<ErrorLine error={goals.error} />{/if}
<GoalsOverview goals={goals.data?.goals ?? []} all />

</section>
<style>
  .all-goals-page { padding-top:var(--page-top); width:100%; max-width:var(--page-measure); min-width:0; margin-inline:auto; }
</style>
