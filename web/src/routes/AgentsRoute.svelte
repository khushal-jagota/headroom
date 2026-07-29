<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import ResourceState from "../components/ResourceState.svelte";
  import { queries } from "../lib/queryCatalogue";

  const workers = createQuery(() => queries.workers());
</script>

<section class="agents-screen" data-screen="agents">
  <div class="agents-page agents-page--index">
    <header class="agents-page-head">
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
        <section class="agents-index-section" data-agents-section>
          <div class="agents-destination-list">
            <a
              class="agents-destination"
              href="#/agents/chief-of-staff"
              data-agent-destination
            >
              <span class="agents-destination-copy">
                <span class="agents-destination-name" data-destination-name>
                  {workers.data.chief_of_staff.label}
                </span>
                <span class="agents-destination-description" data-destination-description>
                  {workers.data.chief_of_staff.skill.description}
                </span>
              </span>
              <span class="agents-destination-arrow" aria-hidden="true">→</span>
            </a>
          </div>
        </section>
      {/if}
    </ResourceState>
  </div>
</section>
