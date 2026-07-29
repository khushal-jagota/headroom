<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import { queries } from "../lib/queryCatalogue";

  let {
    selectedAgent = null
  }: {
    selectedAgent?: "chief-of-staff" | null;
  } = $props();

  const workers = createQuery(() => queries.workers());
  const compactLayoutQuery = window.matchMedia("(max-width: 960px)");
  let compactLayout = $state(compactLayoutQuery.matches);
  let chiefIsSelected = $derived(
    !compactLayout || selectedAgent === "chief-of-staff"
  );

  onMount(() => {
    const onLayoutChange = (event: MediaQueryListEvent) => {
      compactLayout = event.matches;
    };
    compactLayoutQuery.addEventListener("change", onLayoutChange);
    return () => compactLayoutQuery.removeEventListener("change", onLayoutChange);
  });
</script>

<section class="agents-screen" data-screen="agents">
  <div
    class="agents-workspace-shell"
    class:agents-workspace-shell--focused={selectedAgent === "chief-of-staff"}
    data-agents-layout
    data-selected-agent={selectedAgent ?? "default"}
  >
    <aside class="agents-workspace-roster" aria-label="Agents">
      <header class="agents-workspace-roster-head">
        <h1>Agents</h1>
        <p>Work with the agents that help run Panels.</p>
      </header>

      <ResourceState
        error={workers.error}
        loading={workers.isFetching}
        hasData={Boolean(workers.data)}
        loadingText="Loading agents..."
      >
        {#if workers.data}
          <nav class="agents-roster-list" aria-label="Agent conversations">
            <a
              class="agents-roster-row"
              class:active={chiefIsSelected}
              href="#/agents/chief-of-staff"
              data-agent-destination
              data-agent-id="chief-of-staff"
              aria-current={chiefIsSelected ? "page" : undefined}
            >
              <span class="agents-roster-copy">
                <span class="agents-roster-name" data-destination-name>
                  {workers.data.chief_of_staff.label}
                </span>
                <span class="agents-roster-description" data-destination-description>
                  {workers.data.chief_of_staff.skill.description}
                </span>
              </span>
              <span class="agents-roster-arrow" aria-hidden="true">→</span>
            </a>
          </nav>
        {/if}
      </ResourceState>
    </aside>

    {#if chiefIsSelected}
      <section class="agents-workspace-conversation" aria-label="Chief of Staff conversation">
        <header class="agents-conversation-head">
          <a class="agents-conversation-back" href="#/agents" aria-label="Back to agents">
            <span aria-hidden="true">←</span>
            Agents
          </a>
          <h1>Chief of Staff</h1>
        </header>
        <div class="agents-conversation-shell">
          <ChiefConversation />
        </div>
      </section>
    {/if}
  </div>
</section>
