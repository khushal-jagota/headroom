<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson, fetchText } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import {
    FIELD_NAMES,
    PRIORITIES,
    ceilingOptions,
    fieldStageVisualState,
    fieldSlot,
  } from "../lib/ui";
  import type {
    CurrentSprintResponse,
    GatewayStatus,
    ProjectsResponse,
    SprintsResponse,
    TicketDetail
  } from "../lib/types";
  import ChatPanel from "../components/ChatPanel.svelte";
  import Chip from "../components/Chip.svelte";
  import ContentDisclosure from "../components/ContentDisclosure.svelte";
  import EnumPill from "../components/EnumPill.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
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

  const ticketInvalidations = [`ticket:${id}`, "board", "queues", "sprint:current"];
  const emptyTicketFieldText = "Not written yet.";
  const emptyTicketRecapText = "No recap yet.";
  const emptyTicketUserNoteText = "No user note yet.";

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

  function approve(): Promise<unknown> {
    return mutateJson(`/api/tickets/${id}/approve`, { method: "POST" }, ticketInvalidations);
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

  onDestroy(() => {
    ticket.dispose();
    sprints.dispose();
    projects.dispose();
    chatStatus.dispose();
    currentSprint.dispose();
  });
</script>

<section
  class="ticket-screen"
  data-screen="ticket"
  data-ticket-id={id}
  data-state={ticket.data?.state}
>
  {#if ticket.error}
    <ErrorLine error={ticket.error} />
  {:else if ticket.loading && !ticket.data}
    <div class="quiet-line">Loading ticket...</div>
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
          <div class="ticket-meta">
            <EnumPill
              value={detail.priority}
              options={PRIORITIES.map((priority) => ({ value: priority, label: priority }))}
              onChange={(priority) => {
                if (priority !== detail.priority) void patch({ priority });
              }}
            />
            <span class="pill">
              <span class="pill-key">due</span>{detail.deadline || ""}
              <input
                class="ticket-deadline-input"
                type="date"
                data-deadline
                value={detail.deadline || ""}
                onchange={(event) => void patch({ deadline: event.currentTarget.value || null })}
              />
            </span>
            {#if detail.sprint_item_id === null || detail.sprint_item_id === undefined}
              <EnumPill
                value={detail.project_id || ""}
                options={projectOptions}
                onChange={(project_id) => void patch({ project_id: project_id || null })}
              />
            {/if}
            {#if detail.sprint_item_id !== null && detail.sprint_item_id !== undefined}
              <span class="pill"><span class="pill-key">sprint</span>{sprintLabel(detail.effective_sprint_id)}</span>
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
            <span data-ticket-status={detail.ticket_status || "empty"}>
              <Chip variant="ticket-status" value={detail.ticket_status || "empty"} />
            </span>
            <button class="pill pill-button" type="button" data-ticket-takeover-toggle onclick={() => void takeover(detail)}>
              {detail.ticket_status === "user_takeover" ? "Release" : "Take over"}
            </button>
            <button class="pill pill-button" type="button" data-copy onclick={() => void copyTicket()}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          {#if detail.state !== "done"}
            <div class="ticket-scope">
              <span data-scope-ceiling>
                <EnumPill
                  keyLabel="approved until"
                  value={detail.ceiling}
                  options={ceilingOptions(detail.state)}
                  onChange={(ceiling) => void saveScope({ ceiling, at_cap: detail.at_cap })}
                />
              </span>
              <span data-scope-atcap>
                <EnumPill
                  keyLabel="then"
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
          <div class="ticket-user-note" data-user-note>
            <ContentDisclosure title="User note" tone="support" section="user-note">
              <InlineEdit
                value={detail.user_note || ""}
                markdown
                multiline
                placeholder="Preserve user guidance, source context, and boundaries..."
                onSave={(raw) => patch({ user_note: raw })}
              />
              {#if !detail.user_note}
                <div class="quiet-line">{emptyTicketUserNoteText}</div>
              {/if}
            </ContentDisclosure>
          </div>

          <div class="ticket-recap" data-recap>
            <ContentDisclosure title="Recap" tone="support" section="recap">
              {#if ["needs_approach", "needs_plan", "in_progress", "needs_review", "done"].includes(detail.state)}
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
            </ContentDisclosure>
          </div>

          <div class="fields">
            {#each FIELD_NAMES as name}
              {@const slot = fieldSlot(detail, name)}
              {@const stageState = fieldStageVisualState(detail, name)}
              <TicketStageSection
                {name}
                {slot}
                {stageState}
                ticketState={detail.state}
                ceiling={detail.ceiling}
                emptyText={emptyTicketFieldText}
                onAccept={(payload) => acceptField(name, payload)}
                onApproveResult={() => approve()}
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
</section>
