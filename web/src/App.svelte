<script lang="ts">
  import { onMount, tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "./lib/queryCatalogue";
  import { connectionStatus, startChangeStream, stopChangeStream } from "./lib/changeStream";
  import BacklogRoute from "./routes/BacklogRoute.svelte";
  import BoardRoute from "./routes/BoardRoute.svelte";
  import ChiefOfStaffRoute from "./routes/ChiefOfStaffRoute.svelte";
  import DayRoute from "./routes/DayRoute.svelte";
  import FilePreviewRoute from "./routes/FilePreviewRoute.svelte";
  import IdeasRoute from "./routes/IdeasRoute.svelte";
  import ReviewRoute from "./routes/ReviewRoute.svelte";
  import SprintRoute from "./routes/SprintRoute.svelte";
  import TicketRoute from "./routes/TicketRoute.svelte";
  import AgentsRoute from "./routes/AgentsRoute.svelte";
  import DevConversationRoute from "./routes/DevConversationRoute.svelte";
  import DevFilePreviewGalleryRoute from "./routes/DevFilePreviewGalleryRoute.svelte";
  import VpsStatusPopover from "./components/VpsStatusPopover.svelte";

  type Route = {
    name: string;
    params: Record<string, string>;
    key: string;
  };

  const review = createQuery(() => queries.review());
  const connectionLabels = {
    connected: "Connected",
    reconnecting: "Reconnecting"
  };
  const navStatusClearancePx = 8;

  let route = $state<Route>(parseRoute());
  let shellNavElement: HTMLElement | null = null;

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
    if (name === "workers") {
      const legacyDetail = segments[1]
        ? `/workers/${encodeURIComponent(decodeRouteSegment(segments[1]))}`
        : "";
      window.location.replace(`#/agents${legacyDetail}`);
      return {
        name: "agents",
        params: segments[1]
          ? { roleKind: "worker", id: decodeRouteSegment(segments[1]) }
          : { roleKind: "index" },
        key: segments[1] ? `agents/workers/${segments[1]}` : "agents"
      };
    }
    if (name === "agents") {
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
    if (route.name === "agents") return route.params.roleKind !== "unknown";
    if (route.name === "dev") {
      return route.params.sub === "conversation" || route.params.sub === "file-preview-gallery";
    }
    return ["day", "review", "chief", "workspace", "board", "backlog", "ideas", "preview"].includes(route.name);
  }

  function scrollActiveNavLinkIntoStatusClearance(): void {
    if (!shellNavElement) return;
    const activeLink = shellNavElement.querySelector<HTMLElement>(".shell-links .nav-link.active");
    const statuses = shellNavElement.querySelector<HTMLElement>(".shell-statuses");
    if (!activeLink || !statuses) return;

    const navRect = shellNavElement.getBoundingClientRect();
    const activeRect = activeLink.getBoundingClientRect();
    const statusRect = statuses.getBoundingClientRect();
    const clearLeft = navRect.left + navStatusClearancePx;
    const clearRight = Math.min(navRect.right, statusRect.left) - navStatusClearancePx;
    const maxScrollLeft = Math.max(0, shellNavElement.scrollWidth - shellNavElement.clientWidth);
    let nextScrollLeft = shellNavElement.scrollLeft;

    if (activeRect.right > clearRight) {
      nextScrollLeft += activeRect.right - clearRight;
    }
    if (activeRect.left < clearLeft) {
      nextScrollLeft -= clearLeft - activeRect.left;
    }

    nextScrollLeft = Math.min(maxScrollLeft, Math.max(0, nextScrollLeft));
    if (Math.abs(nextScrollLeft - shellNavElement.scrollLeft) > 0.5) {
      shellNavElement.scrollLeft = nextScrollLeft;
    }
  }

  async function alignActiveNavLinkAfterDomUpdate(): Promise<void> {
    await tick();
    scrollActiveNavLinkIntoStatusClearance();
  }

  $effect(() => {
    route.key;
    review.data?.running_worker_count;
    $connectionStatus;
    void alignActiveNavLinkAfterDomUpdate();
  });

  onMount(() => {
    const onHash = () => {
      route = parseRoute();
    };
    const onResize = () => scrollActiveNavLinkIntoStatusClearance();
    window.addEventListener("hashchange", onHash);
    window.addEventListener("resize", onResize);
    void alignActiveNavLinkAfterDomUpdate();
    startChangeStream();
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("resize", onResize);
      stopChangeStream();
    };
  });
</script>

<div class="shell">
  <header class="shell-nav" bind:this={shellNavElement}>
    <nav class="shell-links">
      <a class:active={currentNav("day")} class="nav-link" data-screen="day" href="#/day">Day</a>
      <a class:active={currentNav("review")} class="nav-link nav-link--review" data-screen="review" href="#/review">
        Review
        {#if ((review.data?.ticket_decisions || []).length + (review.data?.user_help_requests || []).length) > 0}
          <span class="nav-badge">{(review.data?.ticket_decisions || []).length + (review.data?.user_help_requests || []).length}</span>
        {:else}
          <span class="nav-badge hidden"></span>
        {/if}
      </a>
      <a class:active={currentNav("workspace")} class="nav-link" data-screen="workspace" href="#/workspace">Workspace</a>
      <a class:active={currentNav("sprint")} class="nav-link" data-screen="sprint" href="#/sprint">Sprint</a>
      <a class:active={currentNav("backlog")} class="nav-link" data-screen="backlog" href="#/backlog">Backlog</a>
      <a class:active={currentNav("ideas")} class="nav-link" data-screen="ideas" href="#/ideas">Ideas</a>
      <a class:active={currentNav("agents")} class="nav-link" data-screen="agents" href="#/agents">Agents</a>
    </nav>
    <div class="shell-statuses">
      <VpsStatusPopover />
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
            <AgentsRoute
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
