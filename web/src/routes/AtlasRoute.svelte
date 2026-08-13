<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import ResourceState from "../components/ResourceState.svelte";
  import { atlasWorld } from "../atlas/projection";
  import type { AtlasBuilding } from "../atlas/contracts";

  const projects = createQuery(() => queries.projects());
  const board = createQuery(() => queries.board());
  const workers = createQuery(() => queries.workers());
  const review = createQuery(() => queries.review());
  let selectedBuildingId = $state<string | null>(null);
  let mapElement = $state<HTMLDivElement | null>(null);
  let world = $derived(
    projects.data && board.data && workers.data && review.data
      ? atlasWorld({ projects: projects.data.projects, board: board.data, workers: workers.data, review: review.data })
      : null
  );
  let selectedBuilding = $derived(world?.buildings.find((building) => building.id === selectedBuildingId) ?? null);

  function selectBuilding(building: AtlasBuilding): void {
    selectedBuildingId = building.id;
  }

  function jumpTo(x: number, y: number): void {
    const map = mapElement;
    if (!map) return;
    map.scrollTo({
      left: Math.max(0, (map.scrollWidth * x) / 100 - map.clientWidth / 2),
      top: Math.max(0, (map.scrollHeight * y) / 100 - map.clientHeight / 2),
      behavior: "smooth"
    });
  }
</script>

<section class="atlas-screen" data-screen="atlas">
  <ResourceState
    error={projects.error || board.error || workers.error || review.error}
    loading={projects.isFetching || board.isFetching || workers.isFetching || review.isFetching}
    hasData={Boolean(world)}
    loadingText="Surveying the atlas..."
  >
    {#if world}
      <header class="atlas-topbar">
        <div class="atlas-brand"><span class="atlas-brand-mark">✦</span><span>Nightshift Atlas</span></div>
        <div class="atlas-counters" aria-label="Atlas status">
          <span><b>{world.activeJobCount}</b> active jobs</span>
          <span><b>{world.crewCount}</b> crew</span>
          <a href="#/review"><b>{world.approvalCount}</b> approvals</a>
          <span><b>Usage</b> unavailable</span>
        </div>
        <a class="atlas-panels-link" href="#/workspace">Panels view ↗</a>
      </header>

      <div class="atlas-layout">
        <aside class="atlas-rail" aria-label="Atlas controller">
          <div class="atlas-rail-heading">Districts</div>
          {#each ["northbank", "rivergate", "southfield"] as district}
            <button type="button" onclick={() => jumpTo(district === "northbank" ? 25 : district === "rivergate" ? 53 : 78, district === "northbank" ? 27 : district === "rivergate" ? 52 : 70)}>
              {district}
            </button>
          {/each}
          <div class="atlas-rail-heading">Need you</div>
          {#if world.alerts.length}
            {#each world.alerts as alert (alert.id)}
              <a class="atlas-alert" href={alert.href}>{alert.label}</a>
            {/each}
          {:else}
            <div class="atlas-empty">Clear. No approvals waiting.</div>
          {/if}
        </aside>

        <div class="atlas-map-frame">
          <div class="atlas-map" bind:this={mapElement} aria-label="Nightshift Atlas map">
            <div class="atlas-world">
              <div class="atlas-river atlas-river-one"></div><div class="atlas-river atlas-river-two"></div>
              <div class="atlas-bridge atlas-bridge-one"></div><div class="atlas-bridge atlas-bridge-two"></div>
              <div class="atlas-town-label atlas-town-one">Northbank</div><div class="atlas-town-label atlas-town-two">Rivergate</div><div class="atlas-town-label atlas-town-three">Southfield</div>
              {#each world.buildings as building (building.id)}
                <button
                  type="button"
                  class:atlas-building--active={building.state === "active"}
                  class:atlas-building--selected={selectedBuilding?.id === building.id}
                  class="atlas-building"
                  style={`--building-x:${building.x}%;--building-y:${building.y}%;`}
                  onclick={() => selectBuilding(building)}
                  ondblclick={() => (window.location.hash = building.href)}
                  aria-label={`${building.label}. ${building.activeTicketTitle || "No active job"}`}
                >
                  <span class="atlas-building-roof"></span><span class="atlas-building-wall"></span><span class="atlas-building-name">{building.label}</span>
                </button>
              {/each}
              {#each world.agents as agent (agent.id)}
                <a class="atlas-agent" href={agent.href} style={`--agent-x:${agent.x}%;--agent-y:${agent.y}%;`} aria-label={`${agent.label}, ${agent.role}`}>
                  <span class="atlas-agent-head"></span><span class="atlas-agent-name">{agent.label}</span>
                </a>
              {/each}
              {#if world.buildings.length === 0}
                <div class="atlas-world-empty">The plots are ready. Create a Panels project and its building appears here.</div>
              {/if}
            </div>
          </div>
          <div class="atlas-minimap" aria-label="Map navigator">
            <div class="atlas-minimap-world">
              {#each world.buildings as building (building.id)}
                <button class="atlas-minimap-dot" style={`--building-x:${building.x}%;--building-y:${building.y}%;`} aria-label={`Jump to ${building.label}`} onclick={() => { selectBuilding(building); jumpTo(building.x, building.y); }}></button>
              {/each}
              <span class="atlas-minimap-viewport"></span>
            </div>
          </div>
        </div>

        <aside class="atlas-detail" aria-live="polite">
          {#if selectedBuilding}
            <div class="atlas-detail-kicker">{selectedBuilding.district}</div>
            <h2>{selectedBuilding.label}</h2>
            <p>{selectedBuilding.summary}</p>
            {#if selectedBuilding.activeTicketTitle}
              <div class="atlas-detail-job">Active: {selectedBuilding.activeTicketTitle}</div>
            {:else}
              <div class="atlas-detail-job">No active work in this district.</div>
            {/if}
            <a class="atlas-action" href={selectedBuilding.href}>Open in Panels ↗</a>
          {:else}
            <div class="atlas-detail-kicker">Command map</div>
            <h2>Pick a building</h2>
            <p>Single-click to inspect. Double-click to open its real Panels work.</p>
          {/if}
        </aside>
      </div>
    {/if}
  </ResourceState>
</section>
