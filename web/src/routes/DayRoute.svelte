<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { shortMonthDayLabel, weekdayLabel } from "../lib/dates";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import type { DayResponse } from "../lib/types";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";

  const day = createQuery(() => queries.todayDay());

  type DayBodyField = "brief_take" | "watchout" | "if_today_lands";
  type DayBodyAttr = "day-take-body" | "day-watch-body" | "day-lands-body";

  const sections: {
    field: DayBodyField;
    key: "take" | "watch" | "lands";
    label: string;
    dataAttr: DayBodyAttr;
  }[] = [
    { field: "brief_take", key: "take", label: "Brief take", dataAttr: "day-take-body" },
    { field: "watchout", key: "watch", label: "Watchout", dataAttr: "day-watch-body" },
    { field: "if_today_lands", key: "lands", label: "If today lands", dataAttr: "day-lands-body" }
  ];

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

  function saveField(field: string, raw: string): Promise<unknown> {
    const date = dateSegment();
    return mutateJson(`/api/day/${date}`, { method: "PATCH", body: { [field]: raw } });
  }
</script>

<section class="day-screen" data-screen="day">
  <ResourceState error={day.error} loading={day.isFetching} hasData={Boolean(day.data)} loadingText="Loading day...">
    {#if day.data}
      {@const info = dateLabel(dateSegment())}
      <div class="doc" data-day-overview>
      <div class="col">
        <div class="date" data-day-date>
          {#if info.weekday}
            {info.weekday} <span class="dim">·</span> {info.monthday}
          {:else}
            {info.monthday}
          {/if}
        </div>
        <InlineEdit
          className="focus"
          dataAttr="day-focus"
          value={day.data.focus}
          placeholder="(no focus set)"
          onSave={(raw) => saveField("focus", raw)}
        />
        {#each sections as section}
          <section class={section.key === "take" ? "take" : `block ${section.key}`} data-day-take={section.key === "take" ? "" : undefined} data-day-watch={section.key === "watch" ? "" : undefined} data-day-lands={section.key === "lands" ? "" : undefined}>
            <div class="label">{section.label}</div>
            <div class="body"><InlineEdit
              dataAttr={section.dataAttr}
              value={day.data[section.field as keyof DayResponse]}
              markdown
              multiline
              placeholder="(none)"
              onSave={(raw) => saveField(section.field, raw)}
            /></div>
          </section>
        {/each}
      </div>
    </div>
    {/if}
  </ResourceState>
</section>
