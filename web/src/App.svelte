<script lang="ts">
  import LazyRegion from "./components/LazyRegion.svelte";
  import { cachedImport } from "./lib/lazyViews";
  import ErrorLine from "./components/ErrorLine.svelte";
  import { onMount, onDestroy, tick } from "svelte";
  import { provideNavigationApplication } from "./lib/navigation/application.svelte";
  import { reviewBadgeImpact } from "./lib/reviewNavigationImpact";
  import { reviewBadgeDomMatches } from "./lib/shellDom";
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
  import { canonicalShellHash, parseConfigSelection } from "./lib/shellRouting";
  import { parseUsageSelection, type UsageTab } from "./lib/usageAddress";
  import BoardRoute from "./routes/BoardRoute.svelte";
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

  const navigation = provideNavigationApplication();
  let displayed = $state.raw<import("./lib/navigation/knowledge").EffectiveNavigationView | null>(null);
  $effect(() => { const view = navigation.selectors.effective; if (view) displayed = view; });
  let reviewCount = $derived(displayed ? navigation.presentationFor(displayed).globalCounts?.review ?? navigation.presentationFor(displayed).review.items.length : 0);
  const reviewBadgeObserver = navigation.registerRenderBoundary(`review-badge:${crypto.randomUUID()}`,reviewBadgeImpact);
  onDestroy(() => navigation.disposeRenderBoundary(reviewBadgeObserver));
  let reviewLink = $state<HTMLElement | null>(null);
  $effect(() => {
    const effective = displayed;
    const generation = navigation.state.generation;
    const element = reviewLink;
    const count = reviewCount;
    if (!effective?.runtimeCurrent || !element) return;
    const publication = effective.publication;
    const observation = reviewBadgeObserver.observeSelectors(publication,generation);
    let obsolete = false;
    void tick().then(() => {
      if (!obsolete && element.isConnected && navigation.state.generation === generation
        && navigation.selectors.effective === effective && reviewBadgeDomMatches(element,count)) reviewBadgeObserver.acknowledgeRender(publication,observation);
    });
    return () => { obsolete = true; };
  });

  const feedbackCount = createQuery(() => queries.feedbackCount());
  let route = $state<Route>(parseRoute());
  const routeModule = $derived.by(() => {
    switch (route.name) {
      case "review": return cachedImport("./ReviewRoute.svelte", () => import("./routes/ReviewRoute.svelte"));
      case "ideas": return cachedImport("./IdeasRoute.svelte", () => import("./routes/IdeasRoute.svelte"));
      case "feedback": return cachedImport("./FeedbackRoute.svelte", () => import("./routes/FeedbackRoute.svelte"));
      case "settings": return cachedImport("./SettingsRoute.svelte", () => import("./routes/SettingsRoute.svelte"));
      case "backends": return cachedImport("./BackendsRoute.svelte", () => import("./routes/BackendsRoute.svelte"));
      case "preview": return cachedImport("./FilePreviewRoute.svelte", () => import("./routes/FilePreviewRoute.svelte"));
      default: return null;
    }
  });
  let routeShape = $derived(route.name === "review" ? "block" as const
    : ["ideas", "feedback", "backends"].includes(route.name) ? "rows" as const : "document" as const);

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
    // Usage owns its tab state so period/provider choices survive tab navigation.
    // Its Config detail keys live inside the page, beside the shared editor mount.
    const screenKey = name === "backends" ? "backends" : segments.join("/") || "workspace";
    return { name, params, key: query ? `${screenKey}${query}` : screenKey };
  }

  function currentNav(name: string): boolean {
    if (name === "workspace") return route.name === "workspace";
    return route.name === name;
  }

  function isKnownRoute(): boolean {
    if (route.name === "settings") {
      return route.params.roleKind !== "unknown";
    }
    return [
      "review",
      "workspace",
      "ideas",
      "feedback",
      "backends",
      "preview"
    ].includes(route.name);
  }

  function secondaryRouteActive(): boolean {
    return ["ideas", "feedback", "settings", "backends"].includes(
      route.name
    );
  }

  function screenTitle(): string {
    if (route.name === "workspace") return "Workspace";
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
      <a class:active={currentNav("review")} class="nav-link nav-link--review" data-screen="review" bind:this={reviewLink} href="#/review">
        Review
        {#if reviewCount > 0}
          <span class="nav-badge">{reviewCount}</span>
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
          {#snippet selectedRoute()}
          {#if route.name === "review"}
            {#await import("./routes/ReviewRoute.svelte") then view}
<view.default />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if route.name === "workspace" && route.workspace}
            <BoardRoute address={route.workspace} onDismissed={showDismissedToast} />
          {:else if route.name === "ideas"}
            {#await import("./routes/IdeasRoute.svelte") then view}
<view.default />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if route.name === "feedback"}
            {#await import("./routes/FeedbackRoute.svelte") then view}
<view.default onDismissed={showDismissedToast} />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if route.name === "settings"}
            {#await import("./routes/SettingsRoute.svelte") then view}
<view.default section={route.params.section} roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"} roleId={route.params.id} />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if route.name === "backends"}
            {#await import("./routes/BackendsRoute.svelte") then view}
<view.default tab={route.params.tab as UsageTab} roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"} roleId={route.params.id} />
{:catch error}
<ErrorLine {error} />
{/await}
          {:else if route.name === "preview"}
            {#await import("./routes/FilePreviewRoute.svelte") then view}
<view.default />
{:catch error}
<ErrorLine {error} />
{/await}
          {/if}
          {/snippet}
          {#if routeModule}
            <LazyRegion promise={routeModule} shape={routeShape}>{@render selectedRoute()}</LazyRegion>
          {:else}
            {@render selectedRoute()}
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
