<script lang="ts">
  import { onMount } from "svelte";
  import { provideNavigationApplication } from "./lib/navigation/application.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "./lib/queryCatalogue";
  import { connectionStatus, startChangeStream, stopChangeStream } from "./lib/changeStream";
  import {
    impliedWorkspaceView,
    parseWorkspaceAddress,
    workspaceAddress,
    type WorkspaceAddress,
    type WorkspaceSelection
  } from "./lib/workspaceAddress";
  import SettingsRoute from "./routes/SettingsRoute.svelte";
  import { canonicalShellHash, parseConfigSelection } from "./lib/shellRouting";
  import { parseUsageSelection, type UsageTab } from "./lib/usageAddress";
  import BackendsRoute from "./routes/BackendsRoute.svelte";
  import BoardRoute from "./routes/BoardRoute.svelte";
  import DayRoute from "./routes/DayRoute.svelte";
  import FilePreviewRoute from "./routes/FilePreviewRoute.svelte";
  import IdeasRoute from "./routes/IdeasRoute.svelte";
  import FeedbackRoute from "./routes/FeedbackRoute.svelte";
  import ReviewRoute from "./routes/ReviewRoute.svelte";
  import SprintRoute from "./routes/SprintRoute.svelte";
  import ReleaseUpdate from "./components/ReleaseUpdate.svelte";
  import ShellStatus from "./components/ShellStatus.svelte";
  import FeedbackCapture from "./components/FeedbackCapture.svelte";
  import FeedbackToast from "./components/FeedbackToast.svelte";
  import { feedbackPageContext } from "./lib/feedback";
  import { mutateJson } from "./lib/mutate";

  type Route = {
    name: string;
    params: Record<string, string>;
    key: string;
    // The Workspace screen reads its whole selection from here. Its key never carries
    // the address, so moving around the Workspace does not remount the screen.
    workspace?: WorkspaceAddress;
  };

  provideNavigationApplication();

  const review = createQuery(() => queries.review());
  const feedbackCount = createQuery(() => queries.feedbackCount());
  let route = $state<Route>(parseRoute());
  let moreOpen = $state(false);
  let moreMenuElement = $state<HTMLDivElement | null>(null);
  let moreButtonElement = $state<HTMLButtonElement | null>(null);
  let toast = $state<{
    key: number;
    message: string;
    actionLabel: string | null;
    actionHref: string | null;
    onAction?: () => void;
    duration: number;
  } | null>(null);
  let toastKey = 0;

  function workspaceRoute(address: WorkspaceAddress): Route {
    return { name: "workspace", params: {}, key: "workspace", workspace: address };
  }

  function redirectToWorkspace(selection: WorkspaceSelection): Route {
    window.location.replace(workspaceAddress(selection));
    return workspaceRoute({
      selection,
      view: impliedWorkspaceView(selection),
      openFile: null
    });
  }

  function decodeRouteSegment(segment: string): string {
    try {
      return decodeURIComponent(segment);
    } catch {
      return segment;
    }
  }

  function parseRoute(): Route {
    const incomingHash = window.location.hash;
    const hash = canonicalShellHash(incomingHash);
    if (hash !== incomingHash) window.location.replace(hash);
    if (hash === "" || hash === "#") {
      return redirectToWorkspace({ kind: "chief" });
    }
    const routeText = hash.slice(1);
    const queryIndex = routeText.indexOf("?");
    const path = queryIndex >= 0 ? routeText.slice(0, queryIndex) : routeText;
    const query = queryIndex >= 0 ? routeText.slice(queryIndex) : "";
    const search = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
    const segments = path.split("/").filter(Boolean);
    const name = segments[0] || "workspace";
    const params: Record<string, string> = {};
    if (name === "day" && segments.length === 1) {
      return redirectToWorkspace({ kind: "chief" });
    }
    if (name === "board") {
      return redirectToWorkspace({ kind: "none" });
    }
    if (name === "chief") {
      return redirectToWorkspace({ kind: "chief" });
    }
    if (name === "ticket" && segments[1]) {
      return redirectToWorkspace({ kind: "ticket", id: decodeRouteSegment(segments[1]) });
    }
    if (name === "workspace") {
      const address = parseWorkspaceAddress(hash);
      if (address === null) {
        return { name: "unknown", params: {}, key: "unknown" };
      }
      return workspaceRoute(address);
    }
    if (name === "settings") {
      params.section = segments[1] || "config";
      const detail = segments.slice(2);
      if (params.section !== "config") {
        if (!["notifications", "scheduled-tasks"].includes(params.section) || detail.length) {
          return { name: "unknown", params: {}, key: "unknown" };
        }
      } else {
        const config = parseConfigSelection(detail);
        if (config === null) return { name: "unknown", params: {}, key: "unknown" };
        params.roleKind = config.roleKind;
        if (config.roleId) params.id = config.roleId;
      }
    }
    if (name === "backends") {
      const usage = parseUsageSelection(segments.slice(1));
      if (usage === null) return { name: "unknown", params: {}, key: "unknown" };
      params.tab = usage.tab;
      params.roleKind = usage.roleKind;
      if (usage.roleId) params.id = usage.roleId;
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
    if (name === "sprint" && search.has("sprint")) {
      params.sprint = search.get("sprint") || "";
    }
    // Usage owns its tab state so period/provider choices survive tab navigation.
    // Its Config detail keys live inside the page, beside the shared editor mount.
    const screenKey = name === "backends" ? "backends" : segments.join("/") || "day";
    return { name, params, key: query ? `${screenKey}${query}` : screenKey };
  }

  function currentNav(name: string): boolean {
    if (name === "workspace") return route.name === "workspace";
    return route.name === name;
  }

  function isKnownRoute(): boolean {
    if (route.name === "sprint") {
      return !route.params.sub || route.params.sub === "documents";
    }
    if (route.name === "settings") {
      return route.params.roleKind !== "unknown";
    }
    return [
      "day",
      "review",
      "workspace",
      "ideas",
      "feedback",
      "backends",
      "preview"
    ].includes(route.name);
  }

  function secondaryRouteActive(): boolean {
    return ["sprint", "ideas", "feedback", "settings", "backends"].includes(
      route.name
    );
  }

  function screenTitle(): string {
    if (route.name === "workspace") return "Workspace";
    if (route.name === "day") return "Home";
    return `${route.name.charAt(0).toUpperCase()}${route.name.slice(1)}`;
  }

  function currentFeedbackContext() {
    const detailTitle = document.querySelector<HTMLElement>("[data-feedback-page-title]")?.innerText || "";
    return feedbackPageContext(window.location.hash, detailTitle);
  }

  function showSavedToast(): void {
    toast = {
      key: ++toastKey,
      message: "Saved to Feedback",
      actionLabel: "View",
      actionHref: "#/feedback",
      duration: 4000
    };
  }

  function showDismissedToast(noteId: string): void {
    const undo = async () => {
      toast = null;
      try {
        await mutateJson(`/api/feedback/${encodeURIComponent(noteId)}/reopen`, { method: "POST" });
      } catch {
        toast = {
          key: ++toastKey,
          message: "Could not undo dismissal",
          actionLabel: null,
          actionHref: null,
          duration: 4000
        };
      }
    };
    toast = {
      key: ++toastKey,
      message: "Dismissed",
      actionLabel: "Undo",
      actionHref: null,
      onAction: () => void undo(),
      duration: 6000
    };
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
  <ReleaseUpdate />
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
      <div class="shell-more" class:rail-utility-fallback={route.name === "workspace"} class:rail-utility-fallback--pane={route.workspace?.selection.kind !== "none"}>
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
            <div class="shell-more-group">Work</div>
            <a class:rail-utility-fallback={route.name === "workspace"} class:rail-utility-fallback--pane={route.workspace?.selection.kind !== "none"} class:active={currentNav("ideas")} href="#/ideas" onclick={closeMore}>Ideas</a>
            <a class:rail-utility-fallback={route.name === "workspace"} class:rail-utility-fallback--pane={route.workspace?.selection.kind !== "none"} class:active={currentNav("feedback")} href="#/feedback" aria-label="Feedback" onclick={closeMore}>
              <span>Feedback</span>
              {#if (feedbackCount.data?.open_count || 0) > 0}<span class="fb-count">{feedbackCount.data?.open_count}</span>{/if}
            </a>
            <div class="shell-more-divider"></div>
            <div class="shell-more-group">System</div>
            <a class:active={currentNav("settings")} class:rail-utility-fallback={route.name === "workspace"} class:rail-utility-fallback--pane={route.workspace?.selection.kind !== "none"} href="#/settings" onclick={closeMore}>
              <span>Settings</span>
            </a>
            <a class:active={currentNav("backends")} class:rail-utility-fallback={route.name === "workspace"} class:rail-utility-fallback--pane={route.workspace?.selection.kind !== "none"} href="#/backends" onclick={closeMore}>
              <span>Usage and Backends</span>
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
    <FeedbackCapture context={currentFeedbackContext} openCount={feedbackCount.data?.open_count || 0} onSaved={showSavedToast} />
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
          {:else if route.name === "workspace" && route.workspace}
            <BoardRoute address={route.workspace} />
          {:else if route.name === "sprint"}
            <SprintRoute sub={route.params.sub || "tracking"} selectedItemId={route.params.item || null} sprintId={route.params.sprint || null} />
          {:else if route.name === "ideas"}
            <IdeasRoute />
          {:else if route.name === "feedback"}
            <FeedbackRoute onDismissed={showDismissedToast} />
          {:else if route.name === "settings"}
            <SettingsRoute section={route.params.section} roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"} roleId={route.params.id} />
          {:else if route.name === "backends"}
            <BackendsRoute tab={route.params.tab as UsageTab} roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"} roleId={route.params.id} />
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
  {#if toast}
    {#key toast.key}
      <FeedbackToast
        message={toast.message}
        actionLabel={toast.actionLabel}
        actionHref={toast.actionHref}
        onAction={toast.onAction}
        duration={toast.duration}
        onDone={() => (toast = null)}
      />
    {/key}
  {/if}
</div>

<style>
  /* Keep system destinations reachable when a selected compact pane hides the rail. */
  .shell-more.rail-utility-fallback,
  .shell-more-menu a.rail-utility-fallback { display:none; }
  @media (max-width:960px) {
    .shell-more.rail-utility-fallback--pane { display:block; }
    .shell-more-menu a.rail-utility-fallback--pane { display:flex; }
  }
</style>
