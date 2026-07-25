<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { shortMonthDayLabel } from "../lib/dates";
  import {
    mutateJsonWithResourceEffect,
    resourceCatalogue
  } from "../lib/resourceCatalogue";
  import { labelize } from "../lib/ui";
  import type { AnyRecord } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ResourceState from "../components/ResourceState.svelte";

  let {
    sub = "tracking",
    selectedItemId = null
  }: { sub?: string; selectedItemId?: string | null } = $props();
  const current = resourceCatalogue.currentSprint();

  const noProjectKey = "__no_project__";
  const itemStatusWord: Record<string, string> = {
    in_progress: "in progress",
    todo: "todo",
    blocked: "blocked",
    done: "done"
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

  let documents = $derived(sub === "documents");

  function saveSprint(sprintId: string, field: string, raw: string): Promise<unknown> {
    return mutateJsonWithResourceEffect(
      `/api/sprints/${sprintId}`,
      { method: "PATCH", body: { [field]: raw } },
      { kind: "currentSprintChanged" }
    );
  }

  function allItems(groups: Record<string, AnyRecord[]>): AnyRecord[] {
    return Object.values(groups || {}).flat();
  }

  function totalItems(groups: Record<string, AnyRecord[]>): number {
    return allItems(groups).length;
  }

  // Items regrouped by their project, alphabetically, No project last; itemless
  // projects fall away naturally (only projects that own an item appear).
  function projectGroups(
    groups: Record<string, AnyRecord[]>
  ): Array<{ key: string; label: string; items: AnyRecord[] }> {
    const byProject = new Map<string, { key: string; label: string; items: AnyRecord[] }>();
    for (const item of allItems(groups)) {
      const key = (item.project_id as string) || noProjectKey;
      const label = (item.project as string) || "No project";
      if (!byProject.has(key)) byProject.set(key, { key, label, items: [] });
      byProject.get(key)?.items.push(item);
    }
    return Array.from(byProject.values()).sort((left, right) => {
      if (left.key === noProjectKey) return 1;
      if (right.key === noProjectKey) return -1;
      return left.label.localeCompare(right.label, undefined, { sensitivity: "base" });
    });
  }

  // Done-fraction from the item's Ticket-Stage rollup: done count over total tickets;
  // an em dash when the item has no tickets yet. Dropped tickets are excluded from the
  // denominator — item status ignores them, so a fully-done item reads "2/2 done", not
  // "2/3", when one of its tickets was dropped.
  function doneFraction(item: AnyRecord): string {
    const rollup = (item.rollup as Record<string, number>) || {};
    const total = Object.entries(rollup).reduce(
      (sum, [stage, count]) => (stage === "dropped" ? sum : sum + count),
      0
    );
    if (total === 0) return "—";
    return `${rollup.done || 0}/${total} done`;
  }

  // A sprint date (stored ISO "YYYY-MM-DD") rendered as a month-day label.
  function sprintDate(iso: string): string {
    const parsed = Date.parse(`${iso}T00:00:00`);
    return Number.isNaN(parsed) ? iso : shortMonthDayLabel(new Date(parsed));
  }

  // Day-of-sprint from the sprint dates, clamped: before the start is day 0, after the
  // end is the final day. Derived client-side from today against the sprint range.
  function dayOfSprint(sprint: AnyRecord): string {
    const start = Date.parse(`${sprint.date_start}T00:00:00`);
    const end = Date.parse(`${sprint.date_end}T00:00:00`);
    if (Number.isNaN(start) || Number.isNaN(end)) return "";
    const dayMs = 86_400_000;
    const total = Math.round((end - start) / dayMs) + 1;
    const today = new Date();
    const todayMs = Date.parse(
      `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}T00:00:00`
    );
    let day = Math.round((todayMs - start) / dayMs) + 1;
    day = Math.max(0, Math.min(day, total));
    return `day ${day} of ${total}`;
  }

  // A ticket row reads active when it is in a non-empty working/review state, green
  // when done. Works for both item-ticket projections and loose tickets.
  function ticketIsActive(ticket: AnyRecord): boolean {
    return (
      ticket.has_pending_proposal === true ||
      ticket.ticket_status === "awaiting_approval" ||
      ticket.ticket_status === "agent" ||
      ticket.ticket_status === "paired" ||
      ticket.ticket_status === "user"
    );
  }

  function ticketStageClass(ticket: AnyRecord): string {
    if (ticket.stage === "done") return "tst tst--done";
    if (ticketIsActive(ticket)) return "tst tst--now";
    return "tst";
  }

  function sectionHasContent(sprint: AnyRecord, fields: string[][]): boolean {
    return fields.some((field) => String(sprint[field[0]] || "").trim() !== "");
  }

  async function focusSelectedItem(): Promise<void> {
    if (!selectedItemId || documents || !current.data?.sprint) return;
    await tick();
    const row = document.querySelector<HTMLElement>(
      `[data-screen="sprint"] [data-item-id="${CSS.escape(selectedItemId)}"]`
    );
    if (!row) return;
    if (row instanceof HTMLDetailsElement) row.open = true;
    row.scrollIntoView({ block: "center", inline: "nearest" });
    row.focus({ preventScroll: true });
  }

  $effect(() => {
    void current.data;
    void selectedItemId;
    void documents;
    void focusSelectedItem();
  });

  onDestroy(() => current.dispose());
</script>

<section class="sprint-screen" data-screen="sprint">
  <ResourceState error={current.error} loading={current.loading} hasData={Boolean(current.data)} loadingText="Loading sprint...">
    {#if !current.data?.sprint}
      <div class="quiet-line">No current sprint.</div>
    {:else}
      {@const sprint = current.data.sprint}
      {@const groups = current.data.groups || {}}
      {@const looseTickets = current.data.loose_tickets || []}
      <div class="doc">
      <div class="col">
        {#if documents}
          <a class="sprint-back" href="#/sprint">‹ {sprint.name}</a>
          <header class="sprint-docs-head">
            <h1 class="sprint-docs-title">Sprint documents</h1>
            <div class="sprint-docs-sub">Kickoff, mid-sprint review, and sprint review — the sprint's written record.</div>
          </header>
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
          <header class="sprint-head">
            <h1 class="sprint-title">
              <InlineEdit
                value={sprint.name}
                placeholder="(unnamed sprint)"
                onSave={(raw) => saveSprint(sprint.id, "name", raw)}
              />
            </h1>
            <div class="sprint-meta-line">
              {sprintDate(sprint.date_start)} – {sprintDate(sprint.date_end)}
              <span class="sep">·</span> {dayOfSprint(sprint)}
              <span class="sep">·</span> {(groups.done || []).length} of {totalItems(groups)} done
              <a class="sprint-docs-link" href="#/sprint/documents">Sprint documents ›</a>
            </div>
          </header>

          <section class="sprint-bet">
            <div class="body"><MarkdownBlock text={sprint.primary_bet} /></div>
          </section>

          {#each projectGroups(groups) as group}
            <Disclosure variant="pgroup" chevron="none" defaultOpen={true} data-project-group={group.key}>
              {#snippet summary()}
                <span class="pchev">›</span>
                <span class="plabel">{group.label}</span>
                <span class="pn">{group.items.length}</span>
              {/snippet}
              <div class="pbody">
                {#each group.items as item}
                  <Disclosure
                    variant="item"
                    chevron="leading"
                    class={item.status === "done" ? "item--dim" : ""}
                    data-item-id={item.id}
                    data-item-status={item.status}
                    data-selected={selectedItemId === item.id ? "true" : undefined}
                    tabindex={selectedItemId === item.id ? "-1" : undefined}
                    defaultOpen={selectedItemId === item.id}
                  >
                    {#snippet summary()}
                      <span class="it list-row-title">{item.title}</span>
                      <span class={`st${item.status === "in_progress" ? " st--now" : ""}`}>{itemStatusWord[item.status]}</span>
                      <span class="frac">{doneFraction(item)}</span>
                    {/snippet}
                    <div class="ibody">
                      <div class="chips">
                        <Chip variant="priority" value={item.priority} />
                        {#if item.deadline}<Chip variant="deadline" value={item.deadline} />{/if}
                        {#each item.blocked_by_titles || [] as blockerTitle}
                          <Chip variant="blocked-by" keyLabel="blocked by" value={blockerTitle} />
                        {/each}
                        {#if item.blockers_cleared}<Chip variant="blockers-cleared" />{/if}
                      </div>
                      {#if (item.tickets || []).length}
                        {#each item.tickets || [] as ticket}
                          <a class="trow" href={`#/ticket/${ticket.id}`} data-ticket-id={ticket.id}>
                            <span class="pr">{ticket.priority}</span>
                            <span class="t">{ticket.title}</span>
                            <span class={ticketStageClass(ticket)}>{labelize(ticket.stage, { capitalize: false })}</span>
                          </a>
                        {/each}
                      {:else}
                        <div class="none">No tickets on this item yet.</div>
                      {/if}
                    </div>
                  </Disclosure>
                {/each}
              </div>
            </Disclosure>
          {/each}

          {#if looseTickets.length}
            <Disclosure variant="pgroup" chevron="none" defaultOpen={true} data-project-group="__loose__">
              {#snippet summary()}
                <span class="pchev">›</span>
                <span class="plabel">Loose tickets</span>
                <span class="pn">{looseTickets.length}</span>
              {/snippet}
              <div class="pbody" data-loose>
                {#each looseTickets as ticket}
                  <a class="trow" href={`#/ticket/${ticket.id}`} data-ticket-id={ticket.id}>
                    <span class="pr">{ticket.priority}</span>
                    <span class="t">{ticket.title}</span>
                    <span class={ticketStageClass(ticket)}>{labelize(ticket.stage, { capitalize: false })}</span>
                  </a>
                {/each}
              </div>
            </Disclosure>
          {/if}
        {/if}
      </div>
    </div>
    {/if}
  </ResourceState>
</section>
