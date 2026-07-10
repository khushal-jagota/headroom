<script lang="ts">
  import { onMount } from "svelte";
  import { fetchJson } from "./lib/api";
  import { resource } from "./lib/resources";
  import { startEventStream, stopEventStream } from "./lib/ws";
  import BacklogRoute from "./routes/BacklogRoute.svelte";
  import BoardRoute from "./routes/BoardRoute.svelte";
  import ChiefOfStaffRoute from "./routes/ChiefOfStaffRoute.svelte";
  import DayRoute from "./routes/DayRoute.svelte";
  import FilePreviewRoute from "./routes/FilePreviewRoute.svelte";
  import IdeasRoute from "./routes/IdeasRoute.svelte";
  import ReviewRoute from "./routes/ReviewRoute.svelte";
  import SprintRoute from "./routes/SprintRoute.svelte";
  import TicketRoute from "./routes/TicketRoute.svelte";

  type Route = {
    name: string;
    params: Record<string, string>;
    key: string;
  };

  const queues = resource<{ approvals: unknown[]; running_agents: number }>(
    "queues",
    () => fetchJson("/api/queues")
  );

  let route = $state<Route>(parseRoute());
  let workspaceHideDone = $state(false);

  function decodeRouteSegment(segment: string): string {
    try {
      return decodeURIComponent(segment);
    } catch {
      return segment;
    }
  }

  function parseRoute(): Route {
    const hash = window.location.hash;
    if (hash === "" || hash === "#") {
      window.location.replace("#/day");
      return { name: "day", params: {}, key: "day" };
    }
    const routeText = hash.slice(1);
    const queryIndex = routeText.indexOf("?");
    const path = queryIndex >= 0 ? routeText.slice(0, queryIndex) : routeText;
    const query = queryIndex >= 0 ? routeText.slice(queryIndex) : "";
    const segments = path.split("/").filter(Boolean);
    const name = segments[0] || "day";
    const params: Record<string, string> = {};
    if (name === "ticket" && segments[1]) {
      params.id = segments[1];
    }
    if (name === "workspace" && segments[1]) {
      params.id = decodeRouteSegment(segments[1]);
    }
    if (name === "sprint" && segments[1]) {
      params.sub = segments[1];
    }
    const screenKey = name === "workspace" || name === "board" ? "workspace" : segments.join("/") || "day";
    return { name, params, key: query ? `${screenKey}${query}` : screenKey };
  }

  function currentNav(name: string): boolean {
    if (name === "workspace") return route.name === "workspace" || route.name === "board";
    return route.name === name;
  }

  function isKnownRoute(): boolean {
    if (route.name === "ticket") return Boolean(route.params.id);
    if (route.name === "sprint") {
      return !route.params.sub || route.params.sub === "tracking" || route.params.sub === "overview";
    }
    return ["day", "review", "chief", "workspace", "board", "backlog", "ideas", "preview"].includes(route.name);
  }

  onMount(() => {
    const onHash = () => {
      route = parseRoute();
    };
    window.addEventListener("hashchange", onHash);
    fetchJson<{ ui_debounce_ms: number }>("/api/meta")
      .then((meta) => startEventStream({ debounceMs: meta.ui_debounce_ms }))
      .catch(() => startEventStream({ debounceMs: 250 }));
    return () => {
      window.removeEventListener("hashchange", onHash);
      stopEventStream();
      queues.dispose();
    };
  });
</script>

<div class="shell">
  <header class="shell-nav">
    <nav class="shell-links">
      <a class:active={currentNav("day")} class="nav-link" data-screen="day" href="#/day">Day</a>
      <a class:active={currentNav("review")} class="nav-link nav-link--review" data-screen="review" href="#/review">
        Review
        {#if (queues.data?.approvals || []).length > 0}
          <span class="nav-badge">{(queues.data?.approvals || []).length}</span>
        {:else}
          <span class="nav-badge hidden"></span>
        {/if}
      </a>
      <a class:active={currentNav("workspace")} class="nav-link" data-screen="workspace" href="#/workspace">Workspace</a>
      <a class:active={currentNav("sprint")} class="nav-link" data-screen="sprint" href="#/sprint">Sprint</a>
      <a class:active={currentNav("backlog")} class="nav-link" data-screen="backlog" href="#/backlog">Backlog</a>
      <a class:active={currentNav("ideas")} class="nav-link" data-screen="ideas" href="#/ideas">Ideas</a>
    </nav>
    {#if (queues.data?.running_agents || 0) > 0}
      <span class="shell-presence" data-shell-presence>
        <span class="shell-presence-spin"></span>
        {queues.data?.running_agents} working
      </span>
    {/if}
  </header>

  <main class="shell-content">
    {#if isKnownRoute()}
      {#key route.key}
        <div class="screen screen-enter">
          {#if route.name === "day"}
            <DayRoute />
          {:else if route.name === "chief"}
            <ChiefOfStaffRoute />
          {:else if route.name === "review"}
            <ReviewRoute />
          {:else if route.name === "workspace" || route.name === "board"}
            <BoardRoute bind:hideDone={workspaceHideDone} ticketId={route.params.id} />
          {:else if route.name === "ticket"}
            <TicketRoute id={route.params.id} />
          {:else if route.name === "sprint"}
            <SprintRoute sub={route.params.sub || "tracking"} />
          {:else if route.name === "backlog"}
            <BacklogRoute />
          {:else if route.name === "ideas"}
            <IdeasRoute />
          {:else if route.name === "preview"}
            <FilePreviewRoute />
          {/if}
        </div>
      {/key}
    {:else}
      <div class="screen">
        <div class="quiet-line">no such screen</div>
      </div>
    {/if}
  </main>
</div>
