<script lang="ts">
  import LazyRegion from "./components/LazyRegion.svelte";
  import VpsPage from "./components/vps/VpsPage.svelte";
  import { cachedImport } from "./lib/lazyViews";
  import ErrorLine from "./components/ErrorLine.svelte";
  import { onMount, onDestroy, tick } from "svelte";
  import { provideNavigationApplication } from "./lib/navigation/application.svelte";
  import { reviewBadgeImpact } from "./lib/reviewNavigationImpact";
  import { reviewBadgeDomMatches } from "./lib/shellDom";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "./lib/queryCatalogue";
  import { connectionStatus, startChangeStream, stopChangeStream } from "./lib/changeStream";
  import type { WorkspaceAddress } from "./lib/workspaceAddress";
  import type { UsageTab } from "./lib/usageAddress";
  import BoardRoute from "./routes/BoardRoute.svelte";
  import ReleaseUpdate from "./components/ReleaseUpdate.svelte";
  import ShellStatus from "./components/ShellStatus.svelte";
  import FeedbackCapture from "./components/FeedbackCapture.svelte";
  import FeedbackToast from "./components/FeedbackToast.svelte";
  import { feedbackPageContext } from "./lib/feedback";
  import { mutateJson } from "./lib/mutate";

  import { parseShellDestination, type ShellDestination } from "./lib/shellDestination";
  import { SHELL_DESTINATIONS } from "./lib/shellDestinations";
  import DestinationIcon from "./components/DestinationIcon.svelte";
  const navigation = provideNavigationApplication();
  let displayed = $state.raw<import("./lib/navigation/knowledge").EffectiveNavigationView | null>(null);
  $effect(() => { const view = navigation.selectors.effective; if (view) displayed = view; });
  let reviewCount = $derived(displayed ? navigation.presentationFor(displayed).globalCounts?.review ?? navigation.presentationFor(displayed).review.items.length : 0);
  const reviewBadgeObserver = navigation.registerRenderBoundary(`review-badge:${crypto.randomUUID()}`,reviewBadgeImpact);
  onDestroy(() => navigation.disposeRenderBoundary(reviewBadgeObserver));
  let reviewLinks = $state<HTMLElement[]>([]);
  function observeReviewLink(element: HTMLElement, key: string) {
    if (key !== "review") return;
    reviewLinks = [...reviewLinks, element];
    return { destroy() { reviewLinks = reviewLinks.filter(value => value !== element); } };
  }
  $effect(() => {
    // Width changes choose the visible Review control for the same render boundary.
    void compact;
    const effective = displayed;
    const generation = navigation.state.generation;
    const element = reviewLinks.find(element => element.getClientRects().length > 0) ?? reviewLinks[0];
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
  let route = $state<ShellDestination>(parseRoute());
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

  function parseRoute(): ShellDestination {
    const destination = parseShellDestination(window.location.hash);
    if (destination.hash !== window.location.hash) window.location.replace(destination.hash);
    return destination;
  }
  const emptyAddress: WorkspaceAddress = { selection: { kind: "none" }, view: "managers", openFile: null };
  let workspaceRouteAddress = $derived(route.workspace ?? emptyAddress);
  let selectedDestination = $derived(route.name === "backends" ? "usage" : route.name === "workspace" ? route.workspace?.selection.kind === "utility" ? route.workspace.selection.utility : "workspace" : route.name);
  let feedbackListHref = $derived("#/feedback");
  let capture = $state<FeedbackCapture | null>(null);
  let captureOpen = $state(false);
  let desktopFoot = $state<HTMLElement | null>(null);
  let compactFoot = $state<HTMLElement | null>(null);
  let desktopFeedback = $state<HTMLButtonElement | null>(null);
  let compact = $state(false);
  let captureAnchor = $derived(compact ? compactFoot : desktopFoot);
  function feedbackFocusTarget(): HTMLElement | null {
    // CSS responds to a resize before matchMedia delivers its change event.
    // Pick the control that is visible when focus is actually restored.
    return desktopFeedback?.getClientRects().length ? desktopFeedback : moreButtonElement;
  }
  function openFeedback(event: MouseEvent): void {
    moreOpen = false;
    capture?.toggleCapture(event.currentTarget as HTMLButtonElement);
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
    capture?.closeCapture(false);
    moreOpen = !moreOpen;
  }

  let lastNavigationFocus: HTMLElement | null = null;
  function onWindowFocusIn(event: FocusEvent): void {
    const element = event.target as HTMLElement;
    if (element === document.body) return;
    lastNavigationFocus = desktopFoot?.contains(element) || compactFoot?.contains(element) ? element : null;
  }

  function onWindowPointerDown(event: PointerEvent): void {
    const target = event.target as Node;
    if (!desktopFoot?.contains(target) && !compactFoot?.contains(target)) lastNavigationFocus = null;
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
    const media = window.matchMedia("(max-width: 720px)");
    const syncCompact = () => {
      const activeElement = document.activeElement as HTMLElement | null;
      // Hiding a focused control can blur it before the media change is delivered.
      const focusedElement = activeElement === document.body && lastNavigationFocus
        && lastNavigationFocus.getClientRects().length === 0 ? lastNavigationFocus : activeElement;
      const focusedScreen = focusedElement?.closest<HTMLElement>("[data-screen]")?.dataset.screen;
      const restoreFocus = moreOpen || desktopFoot?.contains(focusedElement) || compactFoot?.contains(focusedElement);
      compact = media.matches;
      moreOpen = false;
      if (restoreFocus && !captureOpen) void tick().then(() => {
        let target: HTMLElement | null = null;
        if (focusedScreen === "review") {
          target = reviewLinks.find(element => element.getClientRects().length > 0) ?? null;
        } else if (!compact && focusedScreen === "workspace") {
          target = document.querySelector<HTMLElement>('.workspace-rail [aria-current="page"]')
            ?? document.querySelector<HTMLElement>(".workspace-rail [data-chief-destination]");
        } else if (!compact && focusedScreen && focusedScreen !== "more") {
          target = desktopFoot?.querySelector<HTMLElement>(`[data-screen="${focusedScreen}"]`) ?? null;
        }
        (target ?? feedbackFocusTarget())?.focus({ preventScroll: true });
      });
    };
    syncCompact();
    media.addEventListener("change", syncCompact);
    const onHash = () => {
      route = parseRoute();
      closeMore();
    };
    window.addEventListener("hashchange", onHash);
    startChangeStream();
    return () => {
      media.removeEventListener("change", syncCompact);
      window.removeEventListener("hashchange", onHash);
      stopChangeStream();
    };
  });
</script>

<svelte:window onpointerdown={onWindowPointerDown} onkeydown={onWindowKeydown} onfocusin={onWindowFocusIn} />

<div class="shell">
  <ReleaseUpdate />
  <main class="shell-content">
    <BoardRoute address={workspaceRouteAddress} onDismissed={showDismissedToast} externalPane={route.name === "workspace" ? undefined : destinationPane} railFoot={foot} />
  </main>
  {#if moreOpen}<button type="button" class="compact-scrim" aria-label="Close navigation menu" onclick={closeMore}></button>{/if}
  <nav class="compact-bar workspace-mobile-nav" aria-label="Destinations" bind:this={compactFoot}>
    {#if moreOpen}
      <div class="destination-sheet" bind:this={moreMenuElement} id="destination-sheet" data-shell-more-menu>
        <div class="sheet-grab" aria-hidden="true"></div>
        {#each ["Work", "System"] as group}
          {#if group === "System"}<div class="sheet-divider"></div>{/if}
          <div class="sheet-group">{group}</div>
          {#each SHELL_DESTINATIONS.filter(destination => destination.group === group) as destination}
            {#if destination.key === "feedback"}
              <button type="button" class="sheet-item" aria-label="Feedback" aria-expanded={captureOpen} onclick={openFeedback}><DestinationIcon name={destination.key} />{destination.label}</button>
            {:else}
              <a class="sheet-item" href={destination.href} aria-current={selectedDestination === destination.key ? "page" : undefined} onclick={() => capture?.closeCapture(false)}><DestinationIcon name={destination.key} />{destination.label}</a>
            {/if}
          {/each}
        {/each}
      </div>
    {/if}
    <ShellStatus connectionState={$connectionStatus} />
    <div class="compact-bar-row">
      <a href="#/review" class="bar-item" data-screen="review" aria-current={selectedDestination === "review" ? "page" : undefined} use:observeReviewLink={"review"}>Review<span class="count-badge" data-review-badge class:hidden={reviewCount === 0}>{reviewCount > 0 ? reviewCount : ""}</span></a>
      <a href="#/workspace" class="bar-item" data-screen="workspace" aria-current={selectedDestination === "workspace" ? "page" : undefined}>Workspace</a>
      <button type="button" class="bar-item" data-screen="more" bind:this={moreButtonElement} aria-expanded={moreOpen} aria-controls="destination-sheet" onclick={toggleMore}>More <span class="bar-caret" aria-hidden="true">▾</span></button>
    </div>
  </nav>
  <FeedbackCapture bind:this={capture} bind:open={captureOpen} anchor={captureAnchor} focusTarget={feedbackFocusTarget} context={currentFeedbackContext} openCount={feedbackCount.data?.open_count || 0} viewHref={feedbackListHref} onSaved={showSavedToast} />
  {#if toast}
    {#key toast.key}<FeedbackToast message={toast.message} actionLabel={toast.actionLabel} actionHref={toast.actionHref} onAction={toast.onAction} duration={toast.duration} onDone={() => (toast = null)} />{/key}
  {/if}
</div>

{#snippet foot()}
  <div class="rail-foot" bind:this={desktopFoot}>
    <ShellStatus connectionState={$connectionStatus} />
    <nav class="rail-utilities workspace-utility-nav" aria-label="Destinations">
      {#each SHELL_DESTINATIONS as destination}
        {#if destination.key === "feedback"}
          <button type="button" class="utility" bind:this={desktopFeedback} aria-label="Feedback" aria-current={selectedDestination === "feedback" ? "page" : undefined} aria-expanded={captureOpen} aria-controls="feedback-capture" data-feedback-trigger onclick={openFeedback}>
            <DestinationIcon name={destination.key} /><span class="utility-label">Feedback</span>
          </button>
        {:else}
          <a href={destination.href} class="utility" data-screen={destination.key} aria-label={destination.label} aria-current={selectedDestination === destination.key ? "page" : undefined} use:observeReviewLink={destination.key}>
            <DestinationIcon name={destination.key} />
            {#if destination.key === "review"}<span class="count-badge" data-review-badge class:hidden={reviewCount === 0}>{reviewCount > 0 ? reviewCount : ""}</span>{/if}
            <span class="utility-label">{destination.label}</span>
          </a>
        {/if}
      {/each}
    </nav>
  </div>
{/snippet}

{#snippet destinationPane()}
  {#key route.key}
    <div class="destination-pane" data-utility-scroll class:destination-pane--document={!["review", "preview", "vps"].includes(route.name)} class:destination-pane--vps={route.name === "vps"} class:screen-enter={true}>
      {#snippet selectedRoute()}
        {#if route.name === "review"}
          {#await import("./routes/ReviewRoute.svelte") then view}<view.default />{:catch error}<ErrorLine {error} />{/await}
        {:else if route.name === "ideas"}
          {#await import("./routes/IdeasRoute.svelte") then view}<view.default />{:catch error}<ErrorLine {error} />{/await}
        {:else if route.name === "feedback"}
          {#await import("./routes/FeedbackRoute.svelte") then view}<view.default onDismissed={showDismissedToast} />{:catch error}<ErrorLine {error} />{/await}
        {:else if route.name === "settings"}
          {#await import("./routes/SettingsRoute.svelte") then view}<view.default section={route.params.section} roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"} roleId={route.params.id} />{:catch error}<ErrorLine {error} />{/await}
        {:else if route.name === "backends"}
          {#await import("./routes/BackendsRoute.svelte") then view}<view.default tab={route.params.tab as UsageTab} roleKind={route.params.roleKind as "index" | "agent" | "skill" | "worker"} roleId={route.params.id} />{:catch error}<ErrorLine {error} />{/await}
        {:else if route.name === "preview"}
          {#await import("./routes/FilePreviewRoute.svelte") then view}<view.default />{:catch error}<ErrorLine {error} />{/await}
        {:else if route.name === "vps"}
          <VpsPage />
        {:else}<div class="quiet-line">no such screen</div>{/if}
      {/snippet}
      {#if routeModule}<LazyRegion promise={routeModule} shape={routeShape} layout="fill">{@render selectedRoute()}</LazyRegion>{:else}{@render selectedRoute()}{/if}
    </div>
  {/key}
{/snippet}
