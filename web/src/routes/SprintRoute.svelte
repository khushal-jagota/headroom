<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import type { AnyRecord, CurrentSprintResponse } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ListRow from "../components/ListRow.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";

  let { sub = "tracking" }: { sub?: string } = $props();
  const current = resource<CurrentSprintResponse>("sprint:current", (signal) =>
    fetchJson("/api/sprint/current", { signal })
  );

  const liveOrder = ["in_progress", "todo", "blocked"];
  const settledOrder = ["done"];
  const groupLabel: Record<string, string> = {
    in_progress: "In progress",
    todo: "Todo",
    blocked: "Blocked",
    done: "Done"
  };
  const kickoff = [
    ["limiting_factor", "Limiting factor"],
    ["primary_bet", "Primary bet"],
    ["supports", "Supports"],
    ["premortem", "Premortem"]
  ];
  const mid = [
    ["mid_where_we_stand", "Where we stand"],
    ["mid_whats_changed", "What's changed"],
    ["mid_what_to_adjust", "What to adjust"]
  ];
  const review = [
    ["outcomes", "Outcomes"],
    ["solo_reflection", "Solo reflection"],
    ["joint_discussion", "Joint discussion"],
    ["updates_to_thinking", "Updates to thinking"],
    ["carry_forward", "Carry forward"]
  ];

  let overview = $derived(sub === "overview");

  function saveSprint(sprintId: string, field: string, raw: string): Promise<unknown> {
    return mutateJson(
      `/api/sprints/${sprintId}`,
      { method: "PATCH", body: { [field]: raw } },
      [`sprint:${sprintId}`, "sprint:current", "sprints"]
    );
  }

  function countText(tickets: AnyRecord[] | undefined): string {
    const count = tickets?.length || 0;
    if (count === 0) return "no tickets yet";
    return count === 1 ? "1 ticket" : `${count} tickets`;
  }

  function prettyState(state: string): string {
    return String(state).replace(/_/g, " ");
  }

  function totalItems(groups: Record<string, AnyRecord[]>): number {
    return Object.values(groups || {}).reduce((sum, group) => sum + group.length, 0);
  }

  function sectionHasContent(sprint: AnyRecord, fields: string[][]): boolean {
    return fields.some((field) => String(sprint[field[0]] || "").trim() !== "");
  }

  onDestroy(() => current.dispose());
</script>

<section class="sprint-screen" data-screen="sprint">
  {#if current.error}
    <ErrorLine error={current.error} />
  {:else if current.loading && !current.data}
    <div class="quiet-line">Loading sprint...</div>
  {:else if !current.data?.sprint}
    <div class="quiet-line">No current sprint.</div>
  {:else}
    {@const sprint = current.data.sprint}
    {@const groups = current.data.groups || {}}
    <div class="doc">
      <ScreenHeader class="sprint-header">
        {#snippet titleContent()}
          <h1 class="screen-title">
            <InlineEdit
              value={sprint.name}
              placeholder="(unnamed sprint)"
              onSave={(raw) => saveSprint(sprint.id, "name", raw)}
            />
          </h1>
        {/snippet}
        {#snippet meta()}
          <span class="pill sprint-dates-pill">
            <span class="pill-key">dates</span>
            <span class="sprint-dates">{sprint.date_start} – {sprint.date_end}</span>
          </span>
        {/snippet}
      </ScreenHeader>
      <div class="col">
        <nav class="tabs">
          <a class={`tab${overview ? " cur" : ""}`} href="#/sprint/overview">Sprint Overview</a>
          <a class={`tab${!overview ? " cur" : ""}`} href="#/sprint/tracking">Sprint Tracking</a>
        </nav>
        {#if overview}
          <section class="frame">
            <div class="lead"><MarkdownBlock text={sprint.primary_bet} /></div>
          </section>
          {@const reviewHas = sectionHasContent(sprint, review)}
          {@const midHas = sectionHasContent(sprint, mid)}
          {#each [
            { kind: "kickoff", name: "Kickoff", meta: "set at the start", open: !reviewHas && !midHas, fields: kickoff, refline: "" },
            { kind: "mid", name: "Mid-sprint Review", meta: "mid-sprint", open: reviewHas || midHas, fields: mid, refline: "" },
            { kind: "review", name: "Sprint Review", meta: "end of sprint", open: reviewHas, fields: review, refline: "Written with the Mid-sprint Review above in view — it's the raw material for this retrospective." }
          ] as phase}
            <Disclosure variant="phase" data-phase={phase.kind} defaultOpen={phase.open}>
              {#snippet summary()}
                <span class="pnm">{phase.name}</span>
                <span class="pmeta">{phase.meta}</span>
              {/snippet}
              {#if phase.refline}<div class="refline">{phase.refline}</div>{/if}
              {#each phase.fields as field}
                <div class="field" data-field={field[0]}>
                  <div class="flabel">{field[1]}</div>
                  <div class="fval">
                    <InlineEdit
                      value={sprint[field[0]]}
                      markdown
                      multiline
                      placeholder="(none)"
                      onSave={(raw) => saveSprint(sprint.id, field[0], raw)}
                    />
                  </div>
                </div>
              {/each}
            </Disclosure>
          {/each}
        {:else}
          <section class="frame">
            <div class="lead"><MarkdownBlock text={sprint.primary_bet} /></div>
            <div class="health">{(groups.done || []).length} of {totalItems(groups)} done</div>
          </section>
          {#each liveOrder as status}
            <div class="grp" data-status-group={status}>
              <SectionHeading label={groupLabel[status]} count={(groups[status] || []).length} />
              {#each groups[status] || [] as item}
                <Disclosure variant="item" chevron="leading" data-item-id={item.id}>
                  {#snippet summary()}
                    <span class="it list-row-title">{item.title}</span>
                    <span class="chips">
                      <Chip variant="priority" value={item.priority} />
                      <Chip variant="project" value={item.project} />
                      {#if item.deadline}<Chip variant="deadline" value={item.deadline} />{/if}
                      {#if item.blockers_cleared}<Chip variant="blockers-cleared" />{/if}
                      {#each item.blocked_by_titles || [] as blockerTitle}
                        <span class="chip chip--blocked-by"><span class="k">blocked by</span>{blockerTitle}</span>
                      {/each}
                    </span>
                    <span class="count">{countText(item.tickets)}</span>
                  {/snippet}
                  <div class="tkts">
                    {#if (item.tickets || []).length}
                      {#each item.tickets || [] as ticket}
                        <ListRow variant="ticket" title={ticket.title} href={`#/ticket/${ticket.id}`} data-ticket-id={ticket.id}>
                          {#snippet leading()}<span class={`st st--${ticket.state}`}>{prettyState(ticket.state)}</span>{/snippet}
                          {#snippet trailing()}<span class="pr">{ticket.priority}</span>{/snippet}
                        </ListRow>
                      {/each}
                    {:else}
                      <div class="none">No tickets on this item yet.</div>
                    {/if}
                  </div>
                </Disclosure>
              {/each}
            </div>
          {/each}
          {#each settledOrder as status}
            {#if (groups[status] || []).length}
              <Disclosure variant="settled">
                {#snippet summary()}
                  <SectionHeading label={groupLabel[status]} count={(groups[status] || []).length} variant={status === "done" ? "done" : "settled"} />
                {/snippet}
                <div class="grp settled" data-status-group={status}>
                  {#each groups[status] || [] as item}
                    <Disclosure variant="item" chevron="leading" data-item-id={item.id}>
                      {#snippet summary()}
                        <span class="it list-row-title">{item.title}</span>
                        <span class="chips"><Chip variant="priority" value={item.priority} /><Chip variant="project" value={item.project} /></span>
                        <span class="count">{countText(item.tickets)}</span>
                      {/snippet}
                    </Disclosure>
                  {/each}
                </div>
              </Disclosure>
            {/if}
          {/each}
          {#if current.data.loose_tickets.length}
            {@const looseTickets = current.data.loose_tickets}
            <Disclosure variant="settled">
              {#snippet summary()}
                <SectionHeading label="Loose tickets" count={looseTickets.length} variant="settled" />
              {/snippet}
              <div class="tkts" data-loose>
                {#each looseTickets as ticket}
                  <ListRow variant="ticket" title={ticket.title} href={`#/ticket/${ticket.id}`} data-ticket-id={ticket.id}>
                    {#snippet leading()}<span class={`st st--${ticket.state}`}>{prettyState(ticket.state)}</span>{/snippet}
                    {#snippet trailing()}<span class="pr">{ticket.priority}</span>{/snippet}
                  </ListRow>
                {/each}
              </div>
            </Disclosure>
          {/if}
        {/if}
      </div>
    </div>
  {/if}
</section>
