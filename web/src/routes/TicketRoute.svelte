<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson, fetchText } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import {
    FIELD_NAMES,
    PRIORITIES,
    PROJECTS,
    advanceTarget,
    ceilingOptions,
    fieldIsPassed,
    fieldSlot,
    gatingField
  } from "../lib/ui";
  import type {
    CurrentSprintResponse,
    GatewayStatus,
    SprintsResponse,
    TicketDetail,
    TicketField
  } from "../lib/types";
  import ApprovalBlock from "../components/ApprovalBlock.svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import Chip from "../components/Chip.svelte";
  import CollapsibleField from "../components/CollapsibleField.svelte";
  import EnumPill from "../components/EnumPill.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ProposalCard from "../components/ProposalCard.svelte";

  let { id }: { id: string } = $props();

  const ticket = resource<TicketDetail>(`ticket:${id}`, (signal) =>
    fetchJson(`/api/tickets/${id}`, { signal })
  );
  const sprints = resource<SprintsResponse>("sprints", (signal) =>
    fetchJson("/api/sprints", { signal })
  );
  const chatStatus = resource<GatewayStatus>(`chat-status:${id}`, (signal) =>
    fetchJson(`/api/chat/${id}/status`, { signal })
  );
  const currentSprint = resource<CurrentSprintResponse>("sprint:current", (signal) =>
    fetchJson("/api/sprint/current", { signal })
  );

  const ticketInvalidations = [`ticket:${id}`, "board", "queues", "sprint:current"];

  let headerError = $state<unknown>(null);
  let copied = $state(false);

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
      { method: "PUT", body: { note } },
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
    if (!sprintId) return "(none)";
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

  function hasValue(slot: TicketField): boolean {
    return slot.value !== null && slot.value !== undefined && String(slot.value).trim() !== "";
  }

  onDestroy(() => {
    ticket.dispose();
    sprints.dispose();
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
                value={detail.project || ""}
                options={[{ value: "", label: "(no project)" }, ...PROJECTS.map((project) => ({ value: project, label: project }))]}
                onChange={(project) => void patch({ project: project || null })}
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
          <div class="ticket-recap" data-recap>
            <div class="ticket-block-label">Recap</div>
            {#if ["needs_approach", "needs_plan", "in_progress", "needs_review", "done"].includes(detail.state)}
              <div class="ticket-recap-body">
                <InlineEdit
                  value={detail.recap}
                  markdown
                  multiline
                  placeholder="Recap the state of play..."
                  onSave={(raw) =>
                    mutateJson(
                      `/api/tickets/${id}/recap`,
                      { method: "PUT", body: { body: raw } },
                      ticketInvalidations
                    )}
                />
              </div>
            {:else}
              <MarkdownBlock text={detail.recap} />
            {/if}
          </div>

          {#if gatingField(detail.state) && fieldSlot(detail, gatingField(detail.state) || "").proposal}
            {@const gatingName = gatingField(detail.state) || ""}
            {@const slot = fieldSlot(detail, gatingName)}
            <ApprovalBlock
              mode="gating-pending"
              field={gatingName}
              whatLabel={gatingName.replace(/_/g, " ")}
              proposalBody={slot.proposal?.body || ""}
              proposedBy={slot.proposal?.proposed_by || ""}
              note={slot.notes}
              newState={advanceTarget(detail.state, detail.ceiling)}
              onApprove={(payload) => acceptField(gatingName, payload)}
              onNoteSave={(raw) => saveNote(gatingName, raw)}
            />
          {:else if detail.state === "needs_review"}
            {@const slot = fieldSlot(detail, "result")}
            <ApprovalBlock
              mode="needs_review"
              field="result"
              whatLabel="Result"
              proposalBody={slot.value || ""}
              note={slot.notes}
              onApprove={() => approve()}
              onValueSave={(raw) => saveValue("result", raw)}
              onNoteSave={(raw) => saveNote("result", raw)}
            />
          {/if}

          <div class="fields">
            {#each FIELD_NAMES as name}
              {@const slot = fieldSlot(detail, name)}
              {@const isDropped = detail.state === "dropped"}
              {@const isGating = gatingField(detail.state) === name}
              {@const passed = fieldIsPassed(name, detail.state)}
              {@const hasProposal = Boolean(slot.proposal)}
              {@const mark = isDropped ? (hasValue(slot) ? "✓" : "○") : isGating ? "●" : passed || hasValue(slot) ? "✓" : "○"}
              <CollapsibleField {mark} {name} dataField={name}>
                {#if isDropped}
                  <MarkdownBlock text={slot.value} />
                  {#if slot.proposal}
                    <div class="ticket-field-proposal">
                      <div class="proposal-meta">proposed by {slot.proposal.proposed_by}</div>
                      <MarkdownBlock text={slot.proposal.body} />
                    </div>
                  {/if}
                  <div class="note">
                    <div class="note-head">Note</div>
                    <div class="note-body"><InlineEdit value={slot.notes} markdown multiline placeholder="Note..." onSave={(raw) => saveNote(name, raw)} /></div>
                  </div>
                {:else if isGating}
                  {#if hasProposal}
                    <div class="ticket-field-mirror"><MarkdownBlock text={slot.proposal?.body} /></div>
                  {:else}
                    <MarkdownBlock text={slot.value} />
                    <div class="note">
                      <div class="note-head">Note</div>
                      <div class="note-body"><InlineEdit value={slot.notes} markdown multiline placeholder="Note..." onSave={(raw) => saveNote(name, raw)} /></div>
                    </div>
                  {/if}
                {:else if detail.state === "needs_review" && name === "result"}
                  <MarkdownBlock text={slot.value} />
                  {#if slot.proposal}
                    <ProposalCard
                      proposal={slot.proposal}
                      newState={advanceTarget(detail.state, detail.ceiling)}
                      onAccept={(payload) => acceptField(name, payload)}
                    />
                  {/if}
                {:else}
                  {#if slot.proposal}
                    <ProposalCard
                      proposal={slot.proposal}
                      newState={advanceTarget(detail.state, detail.ceiling)}
                      onAccept={(payload) => acceptField(name, payload)}
                    />
                    {#if hasValue(slot)}<MarkdownBlock text={slot.value} />{/if}
                  {:else if passed && hasValue(slot)}
                    <div class="ticket-field-value">
                      <InlineEdit value={slot.value} markdown multiline placeholder="Value..." onSave={(raw) => saveValue(name, raw)} />
                    </div>
                  {:else}
                    <MarkdownBlock text={slot.value} />
                  {/if}
                  <div class="note">
                    <div class="note-head">Note</div>
                    <div class="note-body"><InlineEdit value={slot.notes} markdown multiline placeholder="Note..." onSave={(raw) => saveNote(name, raw)} /></div>
                  </div>
                {/if}
              </CollapsibleField>
            {/each}
          </div>
        </div>
      </main>
      <aside class="chat-rail" data-chat>
        <ChatPanel entityId={id} available={chatStatus.data?.available ?? true} />
      </aside>
    </div>
  {/if}
</section>
