<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { shortMonthDayLabel, weekdayLabel } from "../lib/dates";
  import {
    dayActionTiles,
    dayDotOrder,
    dayPageState,
    dayVisualTicket
  } from "../lib/dayPresentation";
  import { queries } from "../lib/queryCatalogue";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";

  const day = createQuery(() => queries.todayDay());

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

  let tickets = $derived(day.data?.tickets || []);
  let visualTickets = $derived(tickets.map((ticket) => dayVisualTicket(ticket)));
  let dotTickets = $derived(dayDotOrder(visualTickets));
  let actionTiles = $derived(dayActionTiles(visualTickets));
  let pageState = $derived(dayPageState(visualTickets));
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
              {#each dotTickets as visual (visual.ticket.id)}
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
