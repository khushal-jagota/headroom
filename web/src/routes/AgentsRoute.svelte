<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import ChiefConversation from "../components/ChiefConversation.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";
  import { conversationSignalPresentation } from "../lib/conversationSignalPresentation";
  import { queries } from "../lib/queryCatalogue";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";

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
  let howFarThisBrowserHasRead = $state<Record<string, number>>({});

  function rereadWhereThisBrowserHasGot(): void {
    const conversationId = workers.data?.chief_of_staff.conversation_id;
    howFarThisBrowserHasRead =
      typeof conversationId === "string"
        ? { [conversationId]: readReplyWatermark(conversationId) }
        : {};
  }

  let chiefPresentation = $derived(
    workers.data
      ? conversationSignalPresentation(
          workers.data.chief_of_staff,
          howFarThisBrowserHasRead
        )
      : null
  );

  onMount(() => {
    const onLayoutChange = (event: MediaQueryListEvent) => {
      compactLayout = event.matches;
    };
    compactLayoutQuery.addEventListener("change", onLayoutChange);
    const stopListeningForWatermarks = onReplyWatermarkMoved(
      rereadWhereThisBrowserHasGot
    );
    return () => {
      compactLayoutQuery.removeEventListener("change", onLayoutChange);
      stopListeningForWatermarks();
    };
  });

  $effect(() => {
    workers.data;
    rereadWhereThisBrowserHasGot();
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
              <span class="agents-roster-name" data-destination-name>
                {workers.data.chief_of_staff.label}
              </span>
              {#if chiefPresentation}
                <StageMark
                  state={chiefPresentation.state}
                  data-stage-state={chiefPresentation.state}
                  data-needs-me={workers.data.chief_of_staff.needs_me ? "true" : "false"}
                  data-agent-working={workers.data.chief_of_staff.agent_working ? "true" : "false"}
                  data-latest-turn-ended={workers.data.chief_of_staff.latest_turn_ended_sequence}
                  aria-label={chiefPresentation.ariaLabel}
                />
              {/if}
            </a>
          </nav>
        {/if}
      </ResourceState>
    </aside>

    {#if chiefIsSelected}
      <section class="agents-workspace-conversation" aria-label="Chief of Staff conversation">
        <a class="agents-conversation-back" href="#/agents" aria-label="Back to agents">
          <span aria-hidden="true">←</span>
          Agents
        </a>
        <div class="agents-conversation-shell">
          <ChiefConversation />
        </div>
      </section>
    {/if}
  </div>
</section>
