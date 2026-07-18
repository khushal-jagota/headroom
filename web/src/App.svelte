<script lang="ts">
  import { onMount } from "svelte";
  import { fetchJson } from "./lib/api";
  import { markRelayChiefMetaError, resolveRelayChiefFromMeta } from "./lib/capabilities";
  import { resourceCatalogue } from "./lib/resourceCatalogue";
  import { connectionStatus, startEventStream, stopEventStream } from "./lib/ws";
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

  const review = resourceCatalogue.review();
  const connectionLabels = {
    connected: "Connected",
    reconnecting: "Reconnecting",
    offline: "Offline"
  };

  let route = $state<Route>(parseRoute());
  let workspaceHideDone = $state(true);

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
    const search = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
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
      // Legacy sub-routes redirect to the new split: the old two-tab page became a
      // tracking page (#/sprint) and a documents page (#/sprint/documents).
      if (segments[1] === "overview") {
        window.location.replace("#/sprint/documents");
        return { name: "sprint", params: { sub: "documents" }, key: "sprint/documents" };
      }
      if (segments[1] === "tracking") {
        window.location.replace("#/sprint");
        return { name: "sprint", params: {}, key: "sprint" };
      }
      params.sub = segments[1];
    }
    if (name === "sprint" && search.has("item")) {
      params.item = search.get("item") || "";
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
      return !route.params.sub || route.params.sub === "documents";
    }
    return ["day", "review", "chief", "workspace", "board", "backlog", "ideas", "preview"].includes(route.name);
  }

  onMount(() => {
    const onHash = () => {
      route = parseRoute();
    };
    window.addEventListener("hashchange", onHash);
    fetchJson<{ ui_debounce_ms: number; ws_heartbeat_ms: number; relay_chief_enabled?: boolean }>(
      "/api/meta"
    )
      .then((meta) => {
        resolveRelayChiefFromMeta(meta);
        startEventStream({
          debounceMs: meta.ui_debounce_ms,
          heartbeatMs: meta.ws_heartbeat_ms
        });
      })
      .catch(() => {
        // A meta failure must NOT silently mount the legacy Chief pane (F11): resolve the
        // capability to "error" so the Chief routes show a retry placeholder.
        markRelayChiefMetaError();
        startEventStream({ debounceMs: 250 });
      });
    return () => {
      window.removeEventListener("hashchange", onHash);
      stopEventStream();
      review.dispose();
    };
  });
</script>

<div class="shell">
  <header class="shell-nav">
    <nav class="shell-links">
      <a class:active={currentNav("day")} class="nav-link" data-screen="day" href="#/day">Day</a>
      <a class:active={currentNav("review")} class="nav-link nav-link--review" data-screen="review" href="#/review">
        Review
        {#if (review.data?.ticket_decisions || []).length > 0}
          <span class="nav-badge">{(review.data?.ticket_decisions || []).length}</span>
        {:else}
          <span class="nav-badge hidden"></span>
        {/if}
      </a>
      <a class:active={currentNav("workspace")} class="nav-link" data-screen="workspace" href="#/workspace">Workspace</a>
      <a class:active={currentNav("sprint")} class="nav-link" data-screen="sprint" href="#/sprint">Sprint</a>
      <a class:active={currentNav("backlog")} class="nav-link" data-screen="backlog" href="#/backlog">Backlog</a>
      <a class:active={currentNav("ideas")} class="nav-link" data-screen="ideas" href="#/ideas">Ideas</a>
    </nav>
    <div class="shell-statuses">
      {#if (review.data?.running_worker_count || 0) > 0}
        <span class="shell-presence" data-shell-presence>
          <span class="shell-presence-spin" aria-hidden="true"></span>
          {review.data?.running_worker_count} working
        </span>
      {/if}
      <span
        class="shell-connection"
        data-connection-status
        data-state={$connectionStatus}
        role="status"
        aria-live="polite"
      >
        <span class="shell-connection-mark" aria-hidden="true"></span>
        <span class="shell-connection-label">{connectionLabels[$connectionStatus]}</span>
      </span>
    </div>
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
            <SprintRoute sub={route.params.sub || "tracking"} selectedItemId={route.params.item || null} />
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
