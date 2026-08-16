<script lang="ts">
  /** Atlas — the day's work as a place.
   *
   * The screen is two things. Below is the world: an explorable archipelago where a
   * Project is an island, a Sprint Item is a structure on its pad with an overseer,
   * and a Ticket is a worker doing what that Ticket is doing. Above it, when
   * something is picked up, is a real screen of Panels — the Ticket screen, the
   * Sprint Item screen, a supervisor's conversation — not a copy of one.
   *
   * This route owns neither. It reads the board, hands the world a view-model, and
   * raises the right screen over it when the world says something was chosen. The
   * world is three.js and is loaded only here, so no other screen carries it.
   */
  import { onMount, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { buildAtlasWorld, type AtlasWorld } from "../lib/atlas/model";
  import { readSlotBook, writeSlotBook, type SlotBook } from "../lib/atlas/slots";
  import type { AtlasScene, AtlasSelection } from "../lib/atlas/contracts";
  import AtlasPanel from "../components/atlas/AtlasPanel.svelte";
  import AtlasSupervisorConversation from "../components/atlas/AtlasSupervisorConversation.svelte";
  import AtlasReviewStop from "../components/atlas/AtlasReviewStop.svelte";
  import SprintItemWorkspace from "../components/SprintItemWorkspace.svelte";
  import TicketRoute from "./TicketRoute.svelte";

  const board = createQuery(() => queries.board());
  const review = createQuery(() => queries.review());

  let canvas = $state<HTMLCanvasElement | null>(null);
  let scene = $state<AtlasScene | null>(null);
  let selection = $state<AtlasSelection | null>(null);
  // The review walk: a route through the world, worker to worker. While it is on,
  // the panel shows the stop's own proposal rather than whatever it would show for
  // a plain selection.
  let walkIndex = $state<number | null>(null);
  let clock = $state(readClock());

  let slotBook: SlotBook = readSlotBook(typeof localStorage === "undefined" ? null : localStorage);

  const world = $derived.by(() => {
    const built = buildAtlasWorld(board.data, review.data, slotBook);
    slotBook = built.book;
    if (built.slotsGrew) {
      writeSlotBook(typeof localStorage === "undefined" ? null : localStorage, built.book);
    }
    return built.world;
  });

  const proposalStops = $derived(
    (review.data?.items ?? []).filter((item) => item.review_item_type === "proposal")
  );
  const walkStop = $derived(walkIndex === null ? null : (proposalStops[walkIndex] ?? null));

  function readClock(): { time: string; date: string } {
    const now = new Date();
    return {
      time: now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
      date: now
        .toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })
        .toUpperCase()
    };
  }

  const finishedCount = $derived.by(() => {
    let done = 0;
    let total = 0;
    for (const project of world.projects) {
      for (const item of project.items) {
        done += item.doneCount;
        total += item.total;
      }
    }
    return { done, total };
  });

  onMount(() => {
    let live: AtlasScene | null = null;
    let disposed = false;
    const element = untrack(() => canvas);
    if (element) {
      // three.js arrives with this screen and no other.
      void import("../lib/atlas/world").then(({ createAtlasScene }) => {
        if (disposed) return;
        const started: AtlasScene = createAtlasScene({
          canvas: element,
          onSelect: (picked: AtlasSelection | null) => {
            selection = picked;
            if (picked === null) walkIndex = null;
          },
          reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        });
        live = started;
        scene = started;
        started.show(untrack(() => world));
      });
    }
    const tick = window.setInterval(() => (clock = readClock()), 20_000);
    return () => {
      disposed = true;
      window.clearInterval(tick);
      live?.dispose();
      scene = null;
    };
  });

  // Every refresh of the board is a new reading of the world. The scene diffs it and
  // speaks what changed; nothing here decides what is worth saying.
  $effect(() => {
    const reading: AtlasWorld = world;
    scene?.show(reading);
  });

  function closePanel(): void {
    selection = null;
    walkIndex = null;
    scene?.select(null);
  }

  function goHome(): void {
    closePanel();
    scene?.home();
  }

  function startWalk(): void {
    if (proposalStops.length === 0) return;
    moveWalkTo(0);
  }

  function moveWalkTo(index: number): void {
    if (index < 0 || index >= proposalStops.length) {
      // The walk's end: the world sails home and the panel shows nothing.
      goHome();
      return;
    }
    walkIndex = index;
    const stop = proposalStops[index];
    selection = { kind: "ticket", id: stop.ticket_id };
    scene?.select(selection, { travel: true });
  }
</script>

<div class="atlas-screen" data-screen="atlas">
  <canvas class="atlas-canvas" bind:this={canvas} data-atlas-canvas></canvas>

  <div class="atlas-hud">
    <div class="atlas-hud-top">
      <button type="button" class="atlas-wordmark" onclick={goHome} data-atlas-home>ATLAS</button>
      <div class="atlas-clock" data-atlas-clock>
        <span class="atlas-clock-time">{clock.time}</span>
        <span>{clock.date}</span>
        {#if finishedCount.total > 0}
          <span class="atlas-clock-finished"
            >{finishedCount.done} of {finishedCount.total} works stand finished</span
          >
        {/if}
        {#if proposalStops.length > 0 && walkIndex === null}
          <div class="atlas-clock-walk">
            <button type="button" onclick={startWalk} data-atlas-walk>
              {proposalStops.length} to review · walk the queue
            </button>
          </div>
        {/if}
      </div>
    </div>
    {#if !selection}
      <span class="atlas-hint">
        Drag to look · scroll to zoom · click ground to travel, water to sail home
      </span>
    {/if}
  </div>

  <AtlasPanel open={selection !== null} onClose={closePanel}>
    {#if walkStop}
      <AtlasReviewStop
        ticketId={walkStop.ticket_id}
        field={walkStop.field}
        position={(walkIndex ?? 0) + 1}
        total={proposalStops.length}
        onPrevious={() => moveWalkTo((walkIndex ?? 0) - 1)}
        onSkip={() => moveWalkTo((walkIndex ?? 0) + 1)}
        onResolved={() => moveWalkTo(walkIndex ?? 0)}
      />
    {:else if selection?.kind === "ticket" || selection?.kind === "stele"}
      {#key selection.id}
        <TicketRoute id={selection.id} />
      {/key}
    {:else if selection?.kind === "item"}
      {#key selection.id}
        <SprintItemWorkspace itemId={selection.id} sprintName="Atlas" backHref="#/atlas" />
      {/key}
    {:else if selection?.kind === "overseer"}
      {#key selection.id}
        <AtlasSupervisorConversation itemId={selection.id} />
      {/key}
    {/if}
  </AtlasPanel>
</div>
