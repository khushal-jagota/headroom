<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "./lib/queryCatalogue";
  import { connectionStatus, startChangeStream, stopChangeStream } from "./lib/changeStream";
  import BacklogRoute from "./routes/BacklogRoute.svelte";
  import BoardRoute from "./routes/BoardRoute.svelte";
  import DayRoute from "./routes/DayRoute.svelte";
  import FilePreviewRoute from "./routes/FilePreviewRoute.svelte";
  import IdeasRoute from "./routes/IdeasRoute.svelte";
  import ReviewRoute from "./routes/ReviewRoute.svelte";
  import SprintRoute from "./routes/SprintRoute.svelte";
  import TicketRoute from "./routes/TicketRoute.svelte";
  import AgentsRoute from "./routes/AgentsRoute.svelte";
  import ConfigRoute from "./routes/ConfigRoute.svelte";
  import DevConversationRoute from "./routes/DevConversationRoute.svelte";
  import DevFilePreviewGalleryRoute from "./routes/DevFilePreviewGalleryRoute.svelte";
  import ShellStatus from "./components/ShellStatus.svelte";

  type Route = {
    name: string;
    params: Record<string, string>;
    key: string;
  };

  const review = createQuery(() => queries.review());
  let route = $state<Route>(parseRoute());
  let moreOpen = $state(false);
  let moreMenuElement = $state<HTMLDivElement | null>(null);
  let moreButtonElement = $state<HTMLButtonElement | null>(null);

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
    if (name === "chief") {
      window.location.replace("#/agents/chief-of-staff");
      return {
        name: "agents",
        params: { roleKind: "chief" },
        key: "agents"
      };
    }
    if (name === "ticket" && segments[1]) {
      params.id = segments[1];
    }
    if (name === "workspace" && segments[1]) {
      params.id = decodeRouteSegment(segments[1]);
    }
    if (name === "workers") {
      const legacyDetail = segments[1]
        ? `/workers/${encodeURIComponent(decodeRouteSegment(segments[1]))}`
        : "";
      window.location.replace(`#/config${legacyDetail}`);
      return {
        name: "config",
        params: segments[1]
          ? { roleKind: "worker", id: decodeRouteSegment(segments[1]) }
          : { roleKind: "index" },
        key: segments[1] ? `config/workers/${segments[1]}` : "config"
      };
    }
    if (name === "agents") {
      if (segments[1] === "chief-of-staff" && segments.length === 2) {
        params.roleKind = "chief";
      } else if (segments[1] === "worker-skill" && segments.length === 2) {
        window.location.replace("#/config/worker-skill");
        return {
          name: "config",
          params: { roleKind: "skill", id: "panels-worker" },
          key: "config/worker-skill"
        };
      } else if (segments[1] === "workers" && segments[2] && segments.length === 3) {
        const workerType = decodeRouteSegment(segments[2]);
        window.location.replace(`#/config/workers/${encodeURIComponent(workerType)}`);
        return {
          name: "config",
          params: { roleKind: "worker", id: workerType },
          key: `config/workers/${segments[2]}`
        };
      } else if (segments.length === 1) {
        params.roleKind = "index";
      } else {
        params.roleKind = "unknown";
      }
    }
    if (name === "config") {
      if (segments[1] === "chief-of-staff" && segments.length === 2) {
        params.roleKind = "agent";
        params.id = "chief_of_staff";
      } else if (segments[1] === "worker-skill" && segments.length === 2) {
        params.roleKind = "skill";
        params.id = "panels-worker";
      } else if (segments[1] === "workers" && segments[2] && segments.length === 3) {
        params.roleKind = "worker";
        params.id = decodeRouteSegment(segments[2]);
      } else if (segments.length === 1) {
        params.roleKind = "index";
      } else {
        params.roleKind = "unknown";
      }
    }
    // Standalone pages for looking at a system while it is being built or redesigned.
    // Not in the nav, and nothing the app does links to them.
    if (name === "dev" && segments[1]) {
      params.sub = segments[1];
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
    const screenKey =
      name === "workspace" || name === "board"
        ? "workspace"
        : name === "agents"
          ? "agents"
          : segments.join("/") || "day";
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
    if (route.name === "agents" || route.name === "config") {
      return route.params.roleKind !== "unknown";
    }
    if (route.name === "dev") {
      return route.params.sub === "conversation" || route.params.sub === "file-preview-gallery";
    }
    return ["day", "review", "workspace", "board", "backlog", "ideas", "preview"].includes(route.name);
  }

  function secondaryRouteActive(): boolean {
    return ["day", "sprint", "backlog", "ideas", "config"].includes(route.name);
  }

  function screenTitle(): string {
    if (route.name === "board" || route.name === "workspace") return "Workspace";
    if (route.name === "ticket") return "Ticket";
    if (route.name === "agents" && route.params.roleKind === "chief") return "Chief of Staff";
    return `${route.name.charAt(0).toUpperCase()}${route.name.slice(1)}`;
  }

  function closeMore(): void {
    moreOpen = false;
  }

  function toggleMore(): void {
    moreOpen = !moreOpen;
  }

  function onWindowPointerDown(event: PointerEvent): void {
    if (
      moreOpen &&
      moreMenuElement &&
      !moreMenuElement.contains(event.target as Node) &&
      !moreButtonElement?.contains(event.target as Node)
    ) {
      closeMore();
    }
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && moreOpen) {
      closeMore();
      moreButtonElement?.focus();
    }
  }

  onMount(() => {
    const onHash = () => {
      route = parseRoute();
      closeMore();
    };
    window.addEventListener("hashchange", onHash);
    startChangeStream();
    return () => {
      window.removeEventListener("hashchange", onHash);
      stopChangeStream();
    };
  });
</script>

<svelte:window onpointerdown={onWindowPointerDown} onkeydown={onWindowKeydown} />

<div class="shell">
  <header class="shell-mobile-head">
    <span class="shell-mobile-title" data-shell-screen-title>{screenTitle()}</span>
  </header>
  <header class="shell-nav">
    <nav class="shell-links">
      <a class:active={currentNav("review")} class="nav-link nav-link--review" data-screen="review" href="#/review">
        Review
        {#if (review.data?.items || []).length > 0}
          <span class="nav-badge">{(review.data?.items || []).length}</span>
        {:else}
          <span class="nav-badge hidden"></span>
        {/if}
      </a>
      <a class:active={currentNav("workspace")} class="nav-link" data-screen="workspace" href="#/workspace">Workspace</a>
      <a class:active={currentNav("agents")} class="nav-link" data-screen="agents-nav" href="#/agents">Agents</a>
      <div class="shell-more">
        <button
          type="button"
          class="nav-link shell-more-trigger"
          class:active={secondaryRouteActive()}
          bind:this={moreButtonElement}
          aria-expanded={moreOpen}
          aria-controls="shell-more-panel"
          data-screen="more"
          onclick={toggleMore}
        >
          More <span class="shell-more-caret" aria-hidden="true">▾</span>
        </button>
        {#if moreOpen}
          <div
            class="shell-more-menu"
            bind:this={moreMenuElement}
            id="shell-more-panel"
            data-shell-more-menu
          >
            <div class="shell-more-grab" aria-hidden="true"></div>
            <div class="shell-more-group">Planning</div>
            <a class:active={currentNav("day")} href="#/day" onclick={closeMore}>Day</a>
            <a class:active={currentNav("sprint")} href="#/sprint" onclick={closeMore}>Sprint</a>
            <a class:active={currentNav("backlog")} href="#/backlog" onclick={closeMore}>Backlog</a>
            <a class:active={currentNav("ideas")} href="#/ideas" onclick={closeMore}>Ideas</a>
            <div class="shell-more-divider"></div>
            <div class="shell-more-group">System</div>
            <a class:active={currentNav("config")} href="#/config" onclick={closeMore}>
              <span>Config</span>
            </a>
          </div>
        {/if}
      </div>
    </nav>
  </header>
  <div class="shell-statuses">
    <ShellStatus
      connectionState={$connectionStatus}
      runningWorkerCount={review.data?.running_worker_count || 0}
    />
  </div>
  {#if moreOpen}
    <button
      type="button"
      class="shell-more-scrim"
      aria-label="Close navigation menu"
      onclick={closeMore}
    ></button>
  {/if}

  <main class="shell-content">
    {#if isKnownRoute()}
      {#key route.key}
        <div class="screen screen-enter">
          {#if route.name === "day"}
            <DayRoute />
          {:else if route.name === "review"}
            <ReviewRoute />
          {:else if route.name === "workspace" || route.name === "board"}
            <BoardRoute ticketId={route.params.id} />
          {:else if route.name === "ticket"}
            <TicketRoute id={route.params.id} />
          {:else if route.name === "sprint"}
            <SprintRoute sub={route.params.sub || "tracking"} selectedItemId={route.params.item || null} />
          {:else if route.name === "backlog"}
            <BacklogRoute />
          {:else if route.name === "ideas"}
            <IdeasRoute />
          {:else if route.name === "agents"}
            <AgentsRoute selectedAgent={route.params.roleKind === "chief" ? "chief-of-staff" : null} />
          {:else if route.name === "config"}
            <ConfigRoute
              roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"}
              roleId={route.params.id}
            />
          {:else if route.name === "preview"}
            <FilePreviewRoute />
          {:else if route.name === "dev" && route.params.sub === "file-preview-gallery"}
            <DevFilePreviewGalleryRoute />
          {:else if route.name === "dev"}
            <DevConversationRoute />
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
