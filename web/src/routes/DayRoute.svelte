<script lang="ts">
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { shortMonthDayLabel, weekdayLabel } from "../lib/dates";
  import { conversationSignalPresentation } from "../lib/conversationSignalPresentation";
  import { onReplyWatermarkMoved, readReplyWatermark } from "../lib/replyWatermark";
  import type { FieldStageVisualState } from "../lib/ui";
  import { queries } from "../lib/queryCatalogue";
  import type { DayTicket } from "../lib/types";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";

  const day = createQuery(() => queries.todayDay());

  type DayVisualTicket = {
    ticket: DayTicket;
    state: FieldStageVisualState;
    ariaLabel: string;
    group: string;
  };

  type ActionTile = {
    key: "needs-me" | "review" | "working" | "paired" | "done";
    label: string;
    count: number;
    href: string;
  };

  let replyWatermarks = $state<Record<string, number>>({});

  function dateSegment(): string {
    return (day.data?.id || "day_today").slice(4);
  }

  function dateLabel(iso: string): { weekday: string; monthday: string } {
    const parts = iso.split("-");
    if (parts.length !== 3) return { weekday: "", monthday: iso };
    const date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    if (Number.isNaN(date.getTime())) return { weekday: "", monthday: iso };
    return {
      weekday: weekdayLabel(date),
      monthday: shortMonthDayLabel(date)
    };
  }

  function rereadWhereThisBrowserHasGot(): void {
    const positions: Record<string, number> = {};
    for (const ticket of day.data?.tickets || []) {
      if (typeof ticket.conversation_id === "string") {
        positions[ticket.conversation_id] = readReplyWatermark(ticket.conversation_id);
      }
    }
    replyWatermarks = positions;
  }

  onMount(() => onReplyWatermarkMoved(rereadWhereThisBrowserHasGot));

  $effect(() => {
    day.data;
    rereadWhereThisBrowserHasGot();
  });

  function groupKeyFor(ticket: DayTicket): string {
    if (ticket.is_done || ticket.stage === "done") return "done";
    if (ticket.waiting_to_closeout) return "waiting_to_closeout";
    if (
      ticket.ticket_status === "awaiting_approval" &&
      ticket.gating_field === "kickoff"
    ) {
      return "waiting_for_kickoff";
    }
    return String(ticket.ticket_status);
  }

  function visualTicketFor(ticket: DayTicket): DayVisualTicket {
    const group = groupKeyFor(ticket);
    const presentation = conversationSignalPresentation(
      {
        conversation_id:
          typeof ticket.conversation_id === "string" ? ticket.conversation_id : null,
        needs_me: Boolean(ticket.needs_me),
        agent_working: Boolean(ticket.agent_working),
        latest_turn_ended_sequence: Number(ticket.latest_turn_ended_sequence ?? 0)
      },
      replyWatermarks
    );

    if (ticket.is_done || ticket.stage === "done") {
      return { ticket, state: "completed", ariaLabel: "Done", group };
    }
    if (presentation.state === "reply-seen") {
      return { ticket, state: "upcoming", ariaLabel: "Nothing waiting", group };
    }
    if (presentation.state === "upcoming" && group === "paired") {
      return { ticket, state: "current-paired", ariaLabel: "Paired", group };
    }
    if (
      presentation.state === "upcoming" &&
      (group === "awaiting_approval" ||
        group === "waiting_for_kickoff" ||
        group === "needs_user")
    ) {
      return { ticket, state: "current-awaiting-approval", ariaLabel: "To review", group };
    }
    return { ticket, state: presentation.state, ariaLabel: presentation.ariaLabel, group };
  }

  function buildActionTiles(visualTickets: DayVisualTicket[]): ActionTile[] {
    const counts: Record<ActionTile["key"], number> = {
      "needs-me": 0,
      review: 0,
      working: 0,
      paired: 0,
      done: 0
    };

    for (const visual of visualTickets) {
      if (visual.state === "needs-me") counts["needs-me"] += 1;
      else if (visual.state === "current-running") counts.working += 1;
      else if (visual.state === "current-awaiting-approval") counts.review += 1;
      else if (visual.state === "current-paired") counts.paired += 1;
      else if (visual.state === "completed") counts.done += 1;
    }

    const definitions: Array<{
      key: ActionTile["key"];
      label: string;
      href: string;
    }> = [
      { key: "needs-me", label: "Need you", href: "#/workspace" },
      { key: "review", label: "To review", href: "#/review" },
      { key: "working", label: "Working", href: "#/workspace" },
      { key: "paired", label: "Paired", href: "#/workspace" },
      { key: "done", label: "Done", href: "#/workspace" }
    ];

    return definitions
      .filter((definition) => counts[definition.key] > 0)
      .map((definition) => ({ ...definition, count: counts[definition.key] }));
  }

  let tickets = $derived(day.data?.tickets || []);
  let visualTickets = $derived(tickets.map(visualTicketFor));
  let actionTiles = $derived(buildActionTiles(visualTickets));
  let pageState = $derived(
    visualTickets.some((visual) => visual.state === "needs-me") ? "populated" : "calm"
  );
</script>

<section class="day-screen" data-screen="day">
  <ResourceState
    error={day.error}
    loading={day.isFetching}
    hasData={Boolean(day.data)}
    loadingText="Loading day..."
  >
    {#if day.data}
      {@const info = dateLabel(dateSegment())}
      {#if tickets.length === 0}
        <div class="col empty" data-day-state="empty" data-day-overview>
          <div class="date" data-day-date>
            {#if info.weekday}
              {info.weekday} <span class="dim">·</span> {info.monthday}
            {:else}
              {info.monthday}
            {/if}
          </div>
          <h1 class="empty-focus" data-day-focus>No focus set yet</h1>
          <p class="empty-line" data-day-empty-line>The day is empty — nothing planned.</p>
          <a class="plan" href="#/day" data-day-plan>Plan the day <span aria-hidden="true">→</span></a>
        </div>
      {:else}
        <div class="doc" data-day-overview data-day-state={pageState}>
          <div class="col">
            <div class="date" data-day-date>
              {#if info.weekday}
                {info.weekday} <span class="dim">·</span> {info.monthday}
              {:else}
                {info.monthday}
              {/if}
            </div>
            <h1 class="focus" data-day-focus>{day.data.focus || "No focus set yet"}</h1>
            {#if day.data.if_today_lands}
              <p class="lands" data-day-lands>
                <span class="pre">If today lands —</span> {day.data.if_today_lands}
              </p>
            {/if}

            <div class="dots" data-day-dots aria-label="Today's ticket progress">
              {#each visualTickets as visual (visual.ticket.id)}
                <StageMark
                  state={visual.state}
                  data-day-ticket-dot
                  data-ticket-id={visual.ticket.id}
                  data-stage-state={visual.state}
                  aria-label={visual.ariaLabel}
                />
              {/each}
            </div>

            {#if day.data.brief_take}
              <p class="take" data-day-brief-take>{day.data.brief_take}</p>
            {/if}

            {#if actionTiles.length > 0}
              <div class="acts" data-day-actions>
                {#each actionTiles as tile (tile.key)}
                  <a
                    class="act"
                    class:act--you={tile.key === "needs-me"}
                    href={tile.href}
                    data-day-action={tile.key}
                  >
                    <span class="n" data-day-action-count>{tile.count}</span>
                    <span class="k">{tile.label}</span>
                  </a>
                {/each}
              </div>
            {/if}

            {#if day.data.watchout}
              <div class="watch" data-day-watch>
                <span class="lbl">Watch</span>
                <span class="v">{day.data.watchout}</span>
              </div>
            {/if}
          </div>
        </div>
      {/if}
    {/if}
  </ResourceState>
</section>
