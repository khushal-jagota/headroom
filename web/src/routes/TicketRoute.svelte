<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson, fetchText } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import { PRIORITIES, fieldSlot, labelize } from "../lib/ui";
  import { manifestResource } from "../lib/manifest.svelte";
  import {
    ceilingOptionsFor,
    fieldStageVisualStateFor,
    lifecycleFor,
    recapVisibleFor
  } from "../lib/lifecycle";
  import type {
    CurrentSprintResponse,
    GatewayStatus,
    ProjectsResponse,
    SprintsResponse,
    TicketDetail
  } from "../lib/types";
  import ChatPanel from "../components/ChatPanel.svelte";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import EnumPill from "../components/EnumPill.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import Pill from "../components/Pill.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  let { id }: { id: string } = $props();

  const ticket = resource<TicketDetail>(`ticket:${id}`, (signal) =>
    fetchJson(`/api/tickets/${id}`, { signal })
  );
  const sprints = resource<SprintsResponse>("sprints", (signal) =>
    fetchJson("/api/sprints", { signal })
  );
  const projects = resource<ProjectsResponse>("projects", (signal) =>
    fetchJson("/api/projects", { signal })
  );
  const chatStatus = resource<GatewayStatus>(`chat-status:${id}`, (signal) =>
    fetchJson(`/api/chat/${id}/status`, { signal })
  );
  const currentSprint = resource<CurrentSprintResponse>("sprint:current", (signal) =>
    fetchJson("/api/sprint/current", { signal })
  );
  const manifest = manifestResource();

  // Derive the per-Worker-type lifecycle from the RESOURCE (ticket.data?.worker_type), not
  // the markup-local {@const detail} which is only bound inside {#if ticket.data}
  // (Codex F2). Null while the manifest is still loading OR when the Worker type is absent
  // from a loaded manifest; the markup tells those apart via manifest.loading /
  // manifest.error + a type-present check (Codex F3).
  let lc = $derived(lifecycleFor(manifest.data, ticket.data?.worker_type));
  let manifestMissingWorkerType = $derived(
    Boolean(
      ticket.data &&
        manifest.data &&
        !manifest.data.worker_types.some((item) => item.worker_type === ticket.data?.worker_type)
    )
  );

  const ticketInvalidations = [`ticket:${id}`, "board", "queues", "sprint:current"];
  const emptyTicketFieldText = "Not written yet.";
  const emptyTicketRecapText = "No recap yet.";
  const implementerOptions = [
    { value: "", label: "(unassigned)" },
    { value: "khushal", label: "Khushal" },
    { value: "panels_worker", label: "Panels worker" },
    { value: "hermes_codex", label: "Hermes with Codex" },
    { value: "hermes_claude", label: "Hermes with Claude" }
  ];

  let headerError = $state<unknown>(null);
  let copied = $state(false);
  let projectOptions = $derived([
    { value: "", label: "(no project)" },
    ...(projects.data?.projects || []).map((project) => ({ value: project.id, label: project.name }))
  ]);

  function patch(body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${id}`, { method: "PATCH", body }, ticketInvalidations);
  }

  function saveScope(body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${id}/scope`,
      { method: "POST", body },
      ticketInvalidations
    );
  }

  function saveNote(field: string, note: string): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${id}/notes/${field}`,
      { method: "PUT", body: { user_note: note } },
      ticketInvalidations
    );
  }

  function saveValue(field: string, body: string): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${id}/value/${field}`,
      { method: "PUT", body: { body } },
      ticketInvalidations
    );
  }

  function acceptField(field: string, body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${id}/accept/${field}`,
      { method: "POST", body },
      ticketInvalidations
    );
  }

  function writeClipboard(text: string): Promise<void> {
    if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copiedFallback = document.execCommand("copy");
    document.body.removeChild(textarea);
    return copiedFallback ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }

  async function copyTicket(): Promise<void> {
    headerError = null;
    try {
      const text = await fetchText(`/api/tickets/${id}/copy-text`);
      await writeClipboard(text);
      copied = true;
      window.setTimeout(() => (copied = false), 1500);
    } catch (err) {
      headerError = err;
    }
  }

  async function takeover(detail: TicketDetail): Promise<void> {
    const action = detail.ticket_status === "user_takeover" ? "release" : "takeover";
    try {
      await mutateJson(`/api/tickets/${id}/${action}`, { method: "POST" }, ticketInvalidations);
    } catch (err) {
      headerError = err;
    }
  }

  function sprintLabel(sprintId: string | null | undefined): string {
    if (!sprintId) return "no sprint";
    if (sprintId === currentSprint.data?.sprint?.id) return "current";
    return sprints.data?.sprints?.find((sprint) => sprint.id === sprintId)?.name || sprintId;
  }

  function markerFor(detail: TicketDetail): string[] {
    const markers: string[] = [];
    if (detail.ticket_status === "agent_running_step") markers.push("agent-running-step");
    if (detail.ticket_status === "errored") markers.push("errored");
    if (detail.ticket_status === "user_takeover") markers.push("user-takeover");
    if (detail.blocked) markers.push("blocked");
    return markers;
  }

  const STATUS_DISPLAY: Record<string, string> = {
    empty: "empty",
    agent_running_step: "running step",
    awaiting_approval: "awaiting approval",
    user_takeover: "user takeover",
    errored: "errored"
  };

  function statusDisplay(status: string): string {
    return STATUS_DISPLAY[status] || status.replace(/_/g, " ");
  }

  function hasBlockerRows(detail: TicketDetail): boolean {
    const summary = detail.blocker_summary;
    return Boolean(summary && (summary.blocked_by.length > 0 || summary.blocks.length > 0));
  }

  onDestroy(() => {
    ticket.dispose();
    sprints.dispose();
    projects.dispose();
    chatStatus.dispose();
    currentSprint.dispose();
    manifest.dispose();
  });
</script>

<section
  class="ticket-screen"
  data-screen="ticket"
  data-ticket-id={id}
  data-stage={ticket.data?.stage}
>
  <ResourceState error={ticket.error} loading={ticket.loading} hasData={Boolean(ticket.data)} loadingText="Loading ticket...">
    {#if ticket.data && (manifest.error || manifestMissingWorkerType)}
      <div class="ticket-page" data-ticket-manifest-error>
        <ErrorLine
          error={manifest.error ?? { code: "unknown_worker_type", message: `no manifest for Worker type "${ticket.data.worker_type}"` }}
        />
      </div>
    {:else if ticket.data}
      {@const detail = ticket.data}
      <div class="ticket-page">
      <main class="ticket-doc">
        <header class="ticket-head">
          <div class="ticket-title">
            <InlineEdit
              value={detail.title}
              placeholder="Untitled"
              onSave={(raw) => patch({ title: raw })}
            />
          </div>
          <div class="ticket-facts">
            <span
              class="ticket-status-display"
              class:ticket-status-display--attention={(detail.ticket_status || "empty") === "awaiting_approval"}
              data-ticket-status={detail.ticket_status || "empty"}
            >
              <span class="ticket-status-dot"></span>{statusDisplay(detail.ticket_status || "empty")}
            </span>
            <EnumPill
              value={detail.priority}
              options={PRIORITIES.map((priority) => ({ value: priority, label: priority }))}
              onChange={(priority) => {
                if (priority !== detail.priority) void patch({ priority });
              }}
            />
            <span data-implementer>
              <EnumPill
                keyLabel="implementer"
                value={detail.implementer || ""}
                options={implementerOptions}
                onChange={(implementer) => {
                  if (implementer !== (detail.implementer || "")) {
                    void patch({ implementer: implementer || null });
                  }
                }}
              />
            </span>
            <Pill keyLabel="worker type" data-worker-type={detail.worker_type}>
              {lc?.workerTypeLabel ?? labelize(detail.worker_type)}
            </Pill>
            <Pill keyLabel="due">
              {detail.deadline || ""}
              <input
                class="ticket-deadline-input"
                type="date"
                data-deadline
                value={detail.deadline || ""}
                onchange={(event) => void patch({ deadline: event.currentTarget.value || null })}
              />
            </Pill>
            {#if detail.sprint_item_id === null || detail.sprint_item_id === undefined}
              <EnumPill
                value={detail.project_id || ""}
                options={projectOptions}
                onChange={(project_id) => void patch({ project_id: project_id || null })}
              />
            {/if}
            {#if detail.sprint_item_id !== null && detail.sprint_item_id !== undefined}
              <Pill keyLabel="sprint">{sprintLabel(detail.effective_sprint_id)}</Pill>
            {:else}
              <EnumPill
                keyLabel="sprint"
                value={detail.sprint_id || ""}
                options={[{ value: "", label: "(no sprint)" }, ...(sprints.data?.sprints || []).map((sprint) => ({ value: sprint.id, label: sprintLabel(sprint.id) }))]}
                onChange={(sprint_id) => void patch({ sprint_id: sprint_id || null })}
              />
            {/if}
            {#each markerFor(detail) as marker}
              <span data-marker={marker}><Chip variant={marker} value={marker} /></span>
            {/each}
            <span class="ticket-facts-gap"></span>
            {#if detail.stage !== "needs_kickoff"}
              <button class="ticket-act" data-ticket-takeover-toggle="" onclick={() => void takeover(detail)}>
                {detail.ticket_status === "user_takeover" ? "Release" : "Take over"}
              </button>
            {/if}
            <button class="ticket-act" data-copy="" onclick={() => void copyTicket()}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          {#if detail.stage !== "done" && detail.stage !== "needs_kickoff"}
            <div class="ticket-leash">
              approved until
              <span class="ticket-leash-sel" data-scope-ceiling>
                <EnumPill
                  value={detail.ceiling}
                  options={ceilingOptionsFor(lc, detail.stage)}
                  onChange={(ceiling) => void saveScope({ ceiling, at_cap: detail.at_cap })}
                />
              </span>
              then
              <span class="ticket-leash-sel" data-scope-atcap>
                <EnumPill
                  value={detail.at_cap}
                  options={[{ value: "stop", label: "stop" }, { value: "propose", label: "propose" }]}
                  onChange={(at_cap) => void saveScope({ ceiling: detail.ceiling, at_cap })}
                />
              </span>
            </div>
          {/if}
          {#if headerError}<ErrorLine error={headerError} />{/if}
        </header>

        <div class="ticket-col">
          <div class="ticket-recap" data-recap>
            <Disclosure title="Recap" variant="support" defaultOpen={true} data-content-section="recap">
              {#if recapVisibleFor(lc, detail.stage)}
                <InlineEdit
                  value={detail.recap}
                  markdown
                  multiline
                  placeholder="Short orientation for a cold reader..."
                  onSave={(raw) =>
                    mutateJson(
                      `/api/tickets/${id}/recap`,
                      { method: "PUT", body: { body: raw } },
                      ticketInvalidations
                    )}
                />
              {:else}
                <MarkdownBlock text={detail.recap} quiet={emptyTicketRecapText} />
              {/if}
            </Disclosure>
          </div>

          {#if hasBlockerRows(detail)}
            {@const blockerSummary = detail.blocker_summary}
            <section class="ticket-blockers" data-blocker-summary>
              <div class="ticket-blocker-group" data-blocker-group="blocked-by">
                <div class="ticket-blocker-heading">Blocked by</div>
                {#each blockerSummary?.blocked_by || [] as blocker}
                  <a
                    class="ticket-blocker-row"
                    class:ticket-blocker-row--cleared={!blocker.active}
                    href={blocker.href}
                  >
                    <span class="ticket-blocker-title">{blocker.title}</span>
                    <span class="ticket-blocker-state">{blocker.active ? "active" : "cleared"}</span>
                  </a>
                {/each}
              </div>
              <div class="ticket-blocker-group" data-blocker-group="blocks">
                <div class="ticket-blocker-heading">Blocks</div>
                {#each blockerSummary?.blocks || [] as blockedTarget}
                  <a
                    class="ticket-blocker-row"
                    class:ticket-blocker-row--cleared={!blockedTarget.active}
                    href={blockedTarget.href}
                  >
                    <span class="ticket-blocker-title">{blockedTarget.title}</span>
                    <span class="ticket-blocker-state">{blockedTarget.active ? "active" : "cleared"}</span>
                  </a>
                {/each}
              </div>
            </section>
          {/if}

          <div class="fields">
            {#each lc?.fieldIds ?? [] as name}
              {@const slot = fieldSlot(detail, name)}
              {@const stageState = fieldStageVisualStateFor(lc, detail, name)}
              <TicketStageSection
                {name}
                {slot}
                {stageState}
                lifecycle={lc}
                ticketStage={detail.stage}
                ceiling={detail.ceiling}
                emptyText={emptyTicketFieldText}
                onAccept={(payload) => acceptField(name, payload)}
                onSaveNote={(raw) => saveNote(name, raw)}
                onSaveValue={(raw) => saveValue(name, raw)}
              />
            {/each}
          </div>
        </div>
      </main>
      <aside class="chat-rail" data-chat>
        <ChatPanel
          entityId={id}
          available={chatStatus.data?.available ?? true}
        />
      </aside>
    </div>
    {/if}
  </ResourceState>
</section>
