<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchText } from "../lib/api";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { PRIORITIES, fieldSlot, labelize } from "../lib/ui";
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
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import WorkerConfigurationSetup from "../components/WorkerConfigurationSetup.svelte";
  import EnumPill from "../components/EnumPill.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  let { id }: { id: string } = $props();
  const stableId = untrack(() => id);

  const ticket = createQuery(() => queries.ticket(stableId));
  // What a conversation for this Ticket's worker would start on, asked of the server
  // because the server is what resolves it: the Worker type's launch defaults and
  // whatever this Ticket last ran on, through the same code that will create it.
  const conversationStartValues = createQuery(() =>
    queries.ticketConversationStartValues(stableId)
  );
  const sprints = createQuery(() => queries.sprintSummaries());
  const sprintItems = createQuery(() => queries.sprintItems());
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

  let headerError = $state<unknown>(null);
  let copied = $state(false);
  let conversationBackends = $state<readonly BackendSnapshot[]>([]);

  /** How far open this page's conversation is.
   *
   * The state a conversation opens in belongs to the page that shows it, so this page
   * names its own: a Ticket opens at rest — the composer, and above it one line of
   * whatever happened last, against the bottom of the ticket. The person moves it from
   * there and the conversation writes back here when they do.
   */
  let conversationState = $state<ConversationState>("rest");

  /** A click on the ticket drops the conversation back one state.
   *
   * The conversation is the section under the ticket, not a mode it puts the page into,
   * so touching the ticket is how you put it away. Read while the click is still on its
   * way down and neither stopped nor prevented: whatever that click was going to do to
   * the ticket still happens.
   */
  function dropConversationBackOneState(): void {
    if (conversationState === "opened") conversationState = "peeked";
    else if (conversationState === "peeked") conversationState = "rest";
  }

  /** A press in the space either side of the card puts it away, the same as the ticket does.
   *
   * The card is centred in its section, so the section is wider than the card and what is
   * left is page, not conversation. Pressing page is how you put the conversation away, and
   * where on the page it was is not the point.
   */
  function dropConversationOnAPressBesideTheCard(event: MouseEvent): void {
    const pressed = event.target;
    if (!(pressed instanceof Element)) return;
    if (pressed.closest("[data-conversation-pane]") !== null) return;
    dropConversationBackOneState();
  }

  let projectOptions = $derived([
    { value: "", label: "+ project" },
    ...(projects.data?.projects || []).map((project) => ({ value: project.id, label: project.name }))
  ]);
  let sprintItemOptions = $derived([
    { value: "", label: "Backlog" },
    ...(sprintItems.data?.items || []).map((item) => {
      const sprint = item.sprint_id ? sprintLabel(item.sprint_id) : "Unscheduled";
      const fallback = item.kind === "other" ? " · fallback" : "";
      return {
        value: item.id,
        label: `${sprint} · ${item.project} · ${item.title}${fallback}`
      };
    })
  ]);

  onMount(() => {
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

  /** Say that the person here has replied to this Ticket's worker.
   *
   * A Ticket parked on a proposal is waiting for its owner, and a reply is an answer of a
   * kind: it moves to paired. The server owns which statuses move — this says only that a
   * reply happened, and says it after the conversation took the message, because a reply
   * that reached nothing is not a reply.
   *
   * This screen is the one place that knows both halves. The conversation system is told
   * nothing about Tickets, and the send door it offers knows nothing about them either.
   */
  async function recordHumanReply(): Promise<void> {
    try {
      await mutateJson(`/api/tickets/${stableId}/human-reply`, { method: "POST" });
    } catch (err) {
      // The message itself got through. Failing to move the Ticket is worth saying and
      // not worth taking the reply back for.
      headerError = err;
    }
  }

  async function saveSprintItemPlacement(
    detail: TicketDetail,
    sprintItemId: string
  ): Promise<void> {
    try {
      headerError = null;
      if (sprintItemId) {
        await mutateJson(`/api/items/${encodeURIComponent(sprintItemId)}/tickets`, {
          method: "POST",
          body: { ticket_id: stableId }
        });
      } else if (detail.sprint_item_id) {
        await mutateJson(
          `/api/items/${encodeURIComponent(detail.sprint_item_id)}/tickets/${encodeURIComponent(stableId)}`,
          { method: "DELETE" }
        );
      }
    } catch (err) {
      headerError = err;
    }
  }

  /** Start this Ticket's conversation, so the first message has somewhere to go.
   *
   * The readiness loop starts one when it has a step to send; this is what happens when a
   * person gets there first. The reply carries the Ticket, so the id comes back from the
   * same write that made the link.
   */
  async function sendToTicketWorker(body: OwnerSendBody): Promise<DeliveredMessage> {
    return mutateJson<DeliveredMessage>(`/api/tickets/${stableId}/conversation/send`, {
      method: "POST",
      body
    });
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

  function hasExplicitCurrentStageUserOverride(detail: TicketDetail): boolean {
    return currentStageOwnershipOverride(detail) === "user";
  }

  function isWaitingForUser(detail: TicketDetail): boolean {
    return detail.ticket_status === "needs_user";
  }

  function userOwnsCurrentStage(detail: TicketDetail): boolean {
    return (
      detail.effective_stage_ownership_mode === "user" ||
      hasExplicitCurrentStageUserOverride(detail) ||
      isWaitingForUser(detail)
    );
  }

  function saveStageOwner(detail: TicketDetail, ownershipMode: StageOwnershipMode): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${stableId}/stage-ownership/${encodeURIComponent(detail.stage)}`,
      { method: "PUT", body: { ownership_mode: ownershipMode } }
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
    try {
      if (
        userOwnsCurrentStage(detail) &&
        !hasExplicitCurrentStageUserOverride(detail) &&
        !isWaitingForUser(detail)
      ) {
        await saveStageOwner(detail, "worker");
      } else {
        const action = userOwnsCurrentStage(detail) ? "release" : "takeover";
        await mutateJson(`/api/tickets/${stableId}/${action}`, { method: "POST" });
      }
    } catch (err) {
      headerError = err;
    }
  }

  function sprintLabel(sprintId: string | null | undefined): string {
    if (!sprintId) return "";
    if (sprintId === currentSprint.data?.sprint?.id) return "current";
    return sprints.data?.sprints?.find((sprint) => sprint.id === sprintId)?.name || sprintId;
  }

  function formatDeadline(deadline: string | null | undefined): string {
    if (!deadline) return "";
    const [year, month, day] = deadline.split("-").map(Number);
    if (!year || !month || !day) return deadline;
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(
      new Date(year, month - 1, day)
    );
  }

  function priorityNeedsEmphasis(priority: string): boolean {
    return priority === "P0" || priority === "P1";
  }

  function currentStageRunLabel(detail: TicketDetail): string | null {
    if (detail.blocked || detail.ticket_status === "blocked") return null;
    if (detail.ticket_status === "awaiting_approval") return "awaiting approval";
    if (userOwnsCurrentStage(detail)) {
      return "you're on it";
    }
    return null;
  }

  function conversationEmployeeLabel(detail: TicketDetail): string {
    const workerLabel = lc?.workerTypeLabel ?? labelize(detail.worker_type);
    return /worker$/i.test(workerLabel) ? workerLabel : `${workerLabel} worker`;
  }

  // The Kickoff approval card still carries Worker configuration while that
  // choice is editable. Direct blockers belong to the Ticket itself, so they
  // always stay in the masthead instead of moving into a stage card.
  let kickoffCardShowsContextRow = $derived(
    gatingFieldFor(lc, ticket.data?.stage ?? "") === "kickoff" &&
      Boolean(ticket.data?.fields.kickoff.proposal) &&
      Boolean(ticket.data?.employee_configuration_editable)
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
      <!-- The document hears a click only to put the conversation away, and it hears it
           in the capture phase so nothing inside can have gone yet. There is no keyboard
           twin here because Escape does the same thing from anywhere on the page, and it
           belongs to the conversation rather than to the document above it. -->
      <main class="ticket-doc" onclickcapture={dropConversationBackOneState}>
        <header class="ticket-head">
          <div class="ticket-identity" data-ticket-identity>
            <span
              class="ticket-identity-fact ticket-identity-priority"
              class:ticket-identity-priority--urgent={priorityNeedsEmphasis(detail.priority)}
              data-priority-control
              data-priority-alert={priorityNeedsEmphasis(detail.priority) ? detail.priority : undefined}
            >
              {detail.priority}
              <select
                aria-label="Ticket priority"
                value={detail.priority}
                onchange={(event) => {
                  if (event.currentTarget.value !== detail.priority) {
                    void patch({ priority: event.currentTarget.value });
                  }
                }}
              >
                {#each PRIORITIES as priority}
                  <option value={priority}>{priority}</option>
                {/each}
              </select>
            </span>
            <span class="ticket-identity-group">
              <span class="ticket-identity-separator" aria-hidden="true">·</span>
              <span
                class="ticket-identity-fact"
                class:ticket-identity-add={!detail.sprint_item_id}
                data-sprint-item-control
              >
                {#if detail.sprint_item_id}
                  <span class="ticket-identity-key">sprint</span>
                  {sprintLabel(detail.effective_sprint_id)}
                {:else}
                  + add
                {/if}
                <select
                  aria-label={detail.sprint_item_id ? "Ticket sprint placement" : "Add ticket sprint placement"}
                  value={detail.sprint_item_id || ""}
                  onchange={(event) => {
                    const sprintItemId = event.currentTarget.value;
                    if (sprintItemId !== (detail.sprint_item_id || "")) {
                      void saveSprintItemPlacement(detail, sprintItemId);
                    }
                  }}
                >
                  {#each sprintItemOptions as option}
                    <option value={option.value}>{option.label}</option>
                  {/each}
                </select>
              </span>
            </span>
            <span class="ticket-identity-group">
              <span class="ticket-identity-separator" aria-hidden="true">·</span>
              <span
                class="ticket-identity-fact"
                class:ticket-identity-add={!detail.deadline}
                data-deadline-control
              >
                {#if detail.deadline}
                  <span class="ticket-identity-key">due</span>
                  {formatDeadline(detail.deadline)}
                {:else}
                  + add
                {/if}
                <input
                  class="ticket-identity-input"
                  type="date"
                  aria-label={detail.deadline ? "Ticket due date" : "Add ticket due date"}
                  data-deadline
                  value={detail.deadline || ""}
                  onchange={(event) => void patch({ deadline: event.currentTarget.value || null })}
                />
              </span>
            </span>
            {#if detail.sprint_item_id === null || detail.sprint_item_id === undefined}
              <span class="ticket-identity-group">
                <span class="ticket-identity-separator" aria-hidden="true">·</span>
                <span
                  class="ticket-identity-fact"
                  class:ticket-identity-add={!detail.project_id}
                  data-project-control
                >
                  {#if detail.project_id}
                    <span class="ticket-identity-key">project</span>
                    {detail.project}
                  {:else}
                    + add
                  {/if}
                  <select
                    aria-label={detail.project_id ? "Ticket project" : "Add ticket project"}
                    value={detail.project_id || ""}
                    onchange={(event) => void patch({ project_id: event.currentTarget.value || null })}
                  >
                    {#each projectOptions as option}
                      <option value={option.value}>{option.label}</option>
                    {/each}
                  </select>
                </span>
              </span>
            {/if}
          </div>
          <div class="ticket-title-row">
            <div class="ticket-title">
              <InlineEdit
                value={detail.title}
                placeholder="Untitled"
                onSave={(raw) => patch({ title: raw })}
              />
            </div>
          </div>
          <div class="ticket-operating">
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
            <span class="ticket-operating-actions">
              {#if detail.stage !== "needs_kickoff" && detail.stage !== "done"}
                <button class="ticket-act" data-ticket-takeover-toggle="" onclick={() => void takeover(detail)}>
                  {userOwnsCurrentStage(detail) ? "Release" : "Take over"}
                </button>
              {/if}
              <button class="ticket-act ticket-copy" data-copy="" onclick={() => void copyTicket()}>
                {copied ? "Copied" : "Copy"}
              </button>
            </span>
          </div>
          {#if detail.blocker_summary?.blocked_by.length}
            <div class="ticket-blockers" data-blocker-summary>
              <span class="ticket-blocker-heading">Blocked by</span>
              {#each detail.blocker_summary.blocked_by as blocker}
                <span class="ticket-blocker-chip" data-blocker-chip={blocker.ticket_id}>
                  <a class="ticket-blocker-link" href={blocker.href}>{blocker.title}</a>
                  <button
                    type="button"
                    class="ticket-blocker-remove"
                    data-remove-blocker={blocker.ticket_id}
                    aria-label={`Remove blocker ${blocker.title}`}
                    onclick={() => void removeBlocker(blocker.ticket_id)}
                  >×</button>
                </span>
              {/each}
            </div>
          {/if}
          {#if detail.backend_error}
            <div class="ticket-backend-error" data-backend-error role="alert">
              {detail.backend_error}
            </div>
          {/if}
          {#if headerError}<ErrorLine error={headerError} />{/if}
        </header>

        <div class="ticket-col">
          <div class="ticket-recap" data-recap>
            <div class="ticket-recap-inner" data-content-section="recap">
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
            </div>
          </div>

          <div class="fields">
            {#snippet kickoffContextRow()}
              {#if detail.employee_configuration_editable}
                <WorkerConfigurationSetup
                  ticketId={stableId}
                  employeeBackend={detail.employee_backend}
                  employeeLaunchModel={detail.employee_launch_model}
                  employeeLaunchReasoningEffort={detail.employee_launch_reasoning_effort}
                  onSave={saveEmployeeConfiguration}
                />
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
                runLabel={stageState.startsWith("current-") ? currentStageRunLabel(detail) : null}
                runLabelAttention={stageState === "current-awaiting-approval"}
                onRelease={currentStageRunLabel(detail) === "you're on it"
                  ? () => takeover(detail)
                  : undefined}
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
      <div
        class="ticket-conversation-layer"
        data-conversation-layer-host
        onclickcapture={dropConversationOnAPressBesideTheCard}
      >
        <div class="ticket-conversation-column">
          <LiveConversation
            bind:conversationState
            conversationId={detail.conversation_id}
            label={conversationEmployeeLabel(detail)}
            backends={conversationBackends}
            startValues={conversationStartValues.data ?? null}
            senderLabel="owner"
            sendMessage={sendToTicketWorker}
            onNewConversation={resetTicketConversation}
            onMessageAccepted={recordHumanReply}
          />
        </div>
      </div>
    </div>
    {/if}
  </ResourceState>
</section>
