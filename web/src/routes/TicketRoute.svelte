<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchText } from "../lib/api";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { PRIORITIES, fieldSlot, labelize, ticketStatusText } from "../lib/ui";
  import {
    ceilingOptionsFor,
    fieldStageVisualStateFor,
    gatingFieldFor,
    lifecycleFor
  } from "../lib/lifecycle";
  import type {
    EmployeeConfigurationSnapshot,
    StageOwnershipMode,
    TicketDetail
  } from "../lib/types";
  import LiveConversation from "../components/conversation2/LiveConversation.svelte";
  import { readBackends, type BackendSnapshot } from "../lib/conversation2/wire";
  import Button from "../components/Button.svelte";
  import Chip from "../components/Chip.svelte";
  import Disclosure from "../components/Disclosure.svelte";
  import WorkerConfigurationSetup from "../components/WorkerConfigurationSetup.svelte";
  import EnumPill from "../components/EnumPill.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import Pill from "../components/Pill.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  let { id }: { id: string } = $props();
  const stableId = untrack(() => id);

  const ticket = createQuery(() => queries.ticket(stableId));
  const sprints = createQuery(() => queries.sprintSummaries());
  const projects = createQuery(() => queries.projects());
  const currentSprint = createQuery(() => queries.currentSprint());
  const manifest = createQuery(() => queries.workerTypeManifests());

  // Derive the per-Worker-type lifecycle from the QUERY (ticket.data?.worker_type), not
  // the markup-local {@const detail} which is only bound inside {#if ticket.data}
  // (Codex F2). Null while the manifest is still loading OR when the Worker type is absent
  // from a loaded manifest; the markup tells those apart via manifest.isFetching /
  // manifest.error + a type-present check (Codex F3).
  let lc = $derived(lifecycleFor(manifest.data, ticket.data?.worker_type));
  let manifestMissingWorkerType = $derived(
    Boolean(
      ticket.data &&
        manifest.data &&
        !manifest.data.worker_types.some((item) => item.worker_type === ticket.data?.worker_type)
    )
  );
  const emptyTicketFieldText = "Not written yet.";

  const stageOwnerOptions = [
    { value: "", label: "default" },
    { value: "worker", label: "worker" },
    { value: "user", label: "user" },
    { value: "paired", label: "paired" }
  ];

  let headerError = $state<unknown>(null);
  let copied = $state(false);
  let conversationBackends = $state<readonly BackendSnapshot[]>([]);
  let projectOptions = $derived([
    { value: "", label: "(no project)" },
    ...(projects.data?.projects || []).map((project) => ({ value: project.id, label: project.name }))
  ]);

  onMount(() => {
    void mutateJson(`/api/tickets/${stableId}/acknowledge-completed-response`, {
      method: "POST"
    }).catch((err) => {
      headerError = err;
    });
    // What the conversation's model and effort pickers offer. Read once on arrival rather
    // than through the query catalogue: it is a fact about the machine's agents, and
    // nothing a person does to this Ticket changes it.
    void readBackends()
      .then((snapshots) => (conversationBackends = snapshots))
      .catch(() => {
        // The pickers fall back to showing the value already in force, which is the same
        // thing they show before the catalog has arrived. Nothing here is worth a banner.
      });
  });

  /** Start this Ticket's conversation, so the first message has somewhere to go.
   *
   * The readiness loop starts one when it has a step to send; this is what happens when a
   * person gets there first. The reply carries the Ticket, so the id comes back from the
   * same write that made the link.
   */
  async function startTicketConversation(): Promise<string | null> {
    const detail = await mutateJson<TicketDetail>(`/api/tickets/${stableId}/conversation`, {
      method: "POST"
    });
    return detail.employee_session_id;
  }

  /** New: the old conversation is killed and the Ticket stops pointing at it. The next
   *  message starts a fresh one, through the same door as the first one ever did. */
  async function resetTicketConversation(): Promise<void> {
    await mutateJson(`/api/tickets/${stableId}/conversation/reset`, { method: "POST" });
  }

  function patch(body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}`, { method: "PATCH", body });
  }

  function saveScope(body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/scope`, { method: "POST", body });
  }

  function currentStageOwnershipOverride(detail: TicketDetail): StageOwnershipMode | null {
    return detail.stage_ownership_overrides?.[detail.stage] ?? null;
  }

  function currentStageOwnerControlValue(detail: TicketDetail): "" | StageOwnershipMode {
    return currentStageOwnershipOverride(detail) ?? "";
  }

  function hasExplicitCurrentStageUserOverride(detail: TicketDetail): boolean {
    return currentStageOwnershipOverride(detail) === "user";
  }

  function isWaitingForUser(detail: TicketDetail): boolean {
    return detail.ticket_status === "needs_user";
  }

  function canEditCurrentStageOwner(detail: TicketDetail): boolean {
    return detail.default_stage_ownership_mode !== null && detail.effective_stage_ownership_mode !== null;
  }

  function saveStageOwner(detail: TicketDetail, ownershipMode: string): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${stableId}/stage-ownership/${encodeURIComponent(detail.stage)}`,
      { method: "PUT", body: { ownership_mode: ownershipMode || null } }
    );
  }

  function saveNote(field: string, note: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/notes/${field}`, {
      method: "PUT",
      body: { user_note: note }
    });
  }

  function saveValue(field: string, body: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/value/${field}`, {
      method: "PUT",
      body: { body }
    });
  }

  function saveEmployeeConfiguration(
    configuration: EmployeeConfigurationSnapshot
  ): Promise<TicketDetail> {
    return mutateJson<TicketDetail>(`/api/tickets/${stableId}/employee-configuration`, {
      method: "PUT",
      body: configuration
    });
  }

  function acceptField(field: string, body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/accept/${field}`, { method: "POST", body });
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
      const text = await fetchText(`/api/tickets/${stableId}/copy-text`);
      await writeClipboard(text);
      copied = true;
      window.setTimeout(() => (copied = false), 1500);
    } catch (err) {
      headerError = err;
    }
  }

  async function takeover(detail: TicketDetail): Promise<void> {
    const action = hasExplicitCurrentStageUserOverride(detail) || isWaitingForUser(detail)
      ? "release"
      : "takeover";
    try {
      await mutateJson(`/api/tickets/${stableId}/${action}`, { method: "POST" });
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
    if (detail.ticket_status === "agent") markers.push("agent");
    if (detail.ticket_status === "errored") markers.push("errored");
    if (detail.ticket_status === "user") markers.push("user");
    if (detail.ticket_status === "paired") markers.push("paired");
    if (detail.blocked) markers.push("blocked");
    return markers;
  }

  function conversationEmployeeLabel(detail: TicketDetail): string {
    const workerLabel = lc?.workerTypeLabel ?? labelize(detail.worker_type);
    return /worker$/i.test(workerLabel) ? workerLabel : `${workerLabel} worker`;
  }

  function hasBlockerRows(detail: TicketDetail): boolean {
    const summary = detail.blocker_summary;
    return Boolean(summary?.blocked_by.length);
  }

  // The Kickoff approval card carries a context row (Worker configuration and
  // direct blockers) while kickoff is the gating approval. The blocker entries
  // then live in the card, and only then is the standalone section suppressed —
  // both sites share kickoffCardShowsBlockers, so blockers always render in
  // exactly one place.
  let kickoffCardShowsContextRow = $derived(
    gatingFieldFor(lc, ticket.data?.stage ?? "") === "kickoff" &&
      Boolean(ticket.data?.fields.kickoff.proposal) &&
      Boolean(
        ticket.data?.employee_configuration_editable ||
          ticket.data?.blocker_summary?.blocked_by.length
      )
  );
  let kickoffCardShowsBlockers = $derived(
    kickoffCardShowsContextRow && Boolean(ticket.data?.blocker_summary?.blocked_by.length)
  );

  async function removeBlocker(blockerTicketId: string): Promise<void> {
    headerError = null;
    const query = new URLSearchParams({
      from_id: blockerTicketId,
      to_id: stableId,
      kind: "blocks"
    });
    try {
      await mutateJson(`/api/links?${query.toString()}`, { method: "DELETE" });
    } catch (err) {
      headerError = err;
    }
  }
</script>

<section
  class="ticket-screen"
  data-screen="ticket"
  data-ticket-id={stableId}
  data-stage={ticket.data?.stage}
>
  <ResourceState error={ticket.error} loading={ticket.isFetching} hasData={Boolean(ticket.data)} loadingText="Loading ticket...">
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
              class:ticket-status-display--attention={
                ["awaiting_approval", "needs_user"].includes(detail.ticket_status || "empty")
              }
              data-ticket-status={detail.ticket_status || "empty"}
            >
              <span class="ticket-status-dot"></span>{ticketStatusText(detail.ticket_status || "empty")}
            </span>
            <EnumPill
              value={detail.priority}
              options={PRIORITIES.map((priority) => ({ value: priority, label: priority }))}
              onChange={(priority) => {
                if (priority !== detail.priority) void patch({ priority });
              }}
            />

            {#if canEditCurrentStageOwner(detail)}
              <span
                data-stage-owner
                data-owner-mode={currentStageOwnerControlValue(detail) || "default"}
                data-default-owner={detail.default_stage_ownership_mode || ""}
                data-effective-owner={detail.effective_stage_ownership_mode || ""}
              >
                <EnumPill
                  keyLabel="owner"
                  value={currentStageOwnerControlValue(detail)}
                  options={stageOwnerOptions}
                  onChange={(ownershipMode) => {
                    if (ownershipMode !== currentStageOwnerControlValue(detail)) {
                      void saveStageOwner(detail, ownershipMode);
                    }
                  }}
                />
              </span>
            {/if}
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
            {#if detail.stage !== "needs_kickoff" && canEditCurrentStageOwner(detail)}
              <button class="ticket-act" data-ticket-takeover-toggle="" onclick={() => void takeover(detail)}>
                {hasExplicitCurrentStageUserOverride(detail) || isWaitingForUser(detail) ? "Release" : "Take over"}
              </button>
            {/if}
            <button class="ticket-act" data-copy="" onclick={() => void copyTicket()}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          {#if detail.backend_error}
            <div class="ticket-backend-error" data-backend-error role="alert">
              {detail.backend_error}
            </div>
          {/if}
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
                  options={[{ value: "stop", label: "stop" }, { value: "propose", label: "Continue" }]}
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
              <InlineEdit
                value={detail.recap}
                markdown
                multiline
                placeholder="Short orientation for a cold reader..."
                onSave={(raw) =>
                  mutateJson(`/api/tickets/${stableId}/recap`, {
                    method: "PUT",
                    body: { body: raw }
                  })}
              />
            </Disclosure>
          </div>

          {#if hasBlockerRows(detail) && !kickoffCardShowsBlockers}
            {@const blockerSummary = detail.blocker_summary}
            <section class="ticket-blockers" data-blocker-summary>
              <div class="ticket-blocker-group" data-blocker-group="blocked-by">
                <div class="ticket-blocker-heading">Blocked by</div>
                {#each blockerSummary?.blocked_by || [] as blocker}
                  <div class="ticket-blocker-row">
                    <a class="ticket-blocker-link" href={blocker.href}>
                      <span class="ticket-blocker-title">{blocker.title}</span>
                    </a>
                    <button
                      type="button"
                      class="ticket-act"
                      data-remove-blocker={blocker.ticket_id}
                      aria-label={`Remove blocker ${blocker.title}`}
                      onclick={() => void removeBlocker(blocker.ticket_id)}
                    >Remove</button>
                  </div>
                {/each}
              </div>
            </section>
          {/if}

          <div class="fields">
            {#snippet kickoffContextRow()}
              {#if detail.employee_configuration_editable}
                <WorkerConfigurationSetup
                  ticketId={stableId}
                  employeeBackends={manifest.data?.employee_backends ?? []}
                  employeeBackend={detail.employee_backend}
                  employeeLaunchModel={detail.employee_launch_model}
                  employeeLaunchReasoningEffort={detail.employee_launch_reasoning_effort}
                  onSave={saveEmployeeConfiguration}
                />
              {/if}
              {#if kickoffCardShowsBlockers}
                {#each detail.blocker_summary?.blocked_by || [] as blocker}
                  <span class="pill pill--blocker" data-blocker-chip={blocker.ticket_id}>
                    <span class="pill-key">blocked by</span>
                    <a href={blocker.href}>{blocker.title}</a>
                    <Button
                      variant="quiet"
                      data-remove-blocker={blocker.ticket_id}
                      aria-label={`Remove blocker ${blocker.title}`}
                      onclick={() => void removeBlocker(blocker.ticket_id)}
                    >Remove</Button>
                  </span>
                {/each}
              {/if}
            {/snippet}
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
                contextRow={name === "kickoff" && kickoffCardShowsContextRow
                  ? kickoffContextRow
                  : undefined}
                onAccept={(payload) => acceptField(name, payload)}
                onSaveNote={(raw) => saveNote(name, raw)}
                onSaveValue={(raw) => saveValue(name, raw)}
              />
            {/each}
          </div>
        </div>
      </main>
      <aside class="chat-rail" data-chat>
        <LiveConversation
          conversationId={detail.employee_session_id}
          label={conversationEmployeeLabel(detail)}
          backends={conversationBackends}
          senderLabel="owner"
          onStartConversation={startTicketConversation}
          onNewConversation={resetTicketConversation}
        />
      </aside>
    </div>
    {/if}
  </ResourceState>
</section>
