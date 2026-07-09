<script lang="ts">
  import { onMount } from "svelte";
  import { fetchJson } from "./lib/api";
  import { resource } from "./lib/resources";
  import { startEventStream, stopEventStream } from "./lib/ws";
  import BacklogRoute from "./routes/BacklogRoute.svelte";
  import BoardRoute from "./routes/BoardRoute.svelte";
  import ChiefOfStaffRoute from "./routes/ChiefOfStaffRoute.svelte";
  import DayRoute from "./routes/DayRoute.svelte";
  import IdeasRoute from "./routes/IdeasRoute.svelte";
  import ReviewRoute from "./routes/ReviewRoute.svelte";
  import SprintRoute from "./routes/SprintRoute.svelte";
  import TicketRoute from "./routes/TicketRoute.svelte";

  type Route = {
    name: string;
    params: Record<string, string>;
    key: string;
  };

  const queues = resource<{ approvals: unknown[] }>("queues", () => fetchJson("/api/queues"));

  let route = $state<Route>(parseRoute());

  function parseRoute(): Route {
    const hash = window.location.hash;
    if (hash === "" || hash === "#") {
      window.location.replace("#/day");
      return { name: "day", params: {}, key: "day" };
    }
    const segments = hash.slice(1).split("/").filter(Boolean);
    const name = segments[0] || "day";
    const params: Record<string, string> = {};
    if (name === "ticket" && segments[1]) {
      params.id = segments[1];
    }
    if (name === "sprint" && segments[1]) {
      params.sub = segments[1];
    }
    return { name, params, key: segments.join("/") || "day" };
  }

  function currentNav(name: string): boolean {
    return route.name === name;
  }

  function isKnownRoute(): boolean {
    if (route.name === "ticket") return Boolean(route.params.id);
    if (route.name === "sprint") {
      return !route.params.sub || route.params.sub === "tracking" || route.params.sub === "overview";
    }
    return ["day", "review", "chief", "board", "backlog", "ideas"].includes(route.name);
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
    <span class="shell-brand">Panels</span>
    <nav class="shell-links">
      <a class:active={currentNav("day")} class="nav-link" data-screen="day" href="#/day">Day</a>
      <a class:active={currentNav("chief")} class="nav-link" data-screen="chief" href="#/chief">Chief of Staff</a>
      <a class:active={currentNav("review")} class="nav-link" data-screen="review" href="#/review">
        Review
        {#if (queues.data?.approvals || []).length > 0}
          <span class="nav-badge">{(queues.data?.approvals || []).length}</span>
        {:else}
          <span class="nav-badge hidden"></span>
        {/if}
      </a>
      <a class:active={currentNav("board")} class="nav-link" data-screen="board" href="#/board">Board</a>
      <a class:active={currentNav("sprint")} class="nav-link" data-screen="sprint" href="#/sprint">Sprint</a>
      <a class:active={currentNav("backlog")} class="nav-link" data-screen="backlog" href="#/backlog">Backlog</a>
      <a class:active={currentNav("ideas")} class="nav-link" data-screen="ideas" href="#/ideas">Ideas</a>
    </nav>
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
          {:else if route.name === "board"}
            <BoardRoute />
          {:else if route.name === "ticket"}
            <TicketRoute id={route.params.id} />
          {:else if route.name === "sprint"}
            <SprintRoute sub={route.params.sub || "tracking"} />
          {:else if route.name === "backlog"}
            <BacklogRoute />
          {:else if route.name === "ideas"}
            <IdeasRoute />
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
