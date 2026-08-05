<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { fetchText } from "../lib/api";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { fieldSlot, labelize, stageLabel } from "../lib/ui";
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
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";
  import TicketPriorityControl from "../components/TicketPriorityControl.svelte";

  let { id }: { id: string } = $props();
  const stableId = untrack(() => id);

  const ticket = createQuery(() => queries.ticket(stableId));
  // What a conversation for this Ticket's worker would start on, asked of the server
  // because the server is what resolves it: the Worker type's launch defaults and
  // whatever this Ticket last ran on, through the same code that will create it.
  const conversationStartValues = createQuery(() =>
    queries.ticketConversationStartValues(stableId)
  );
  const projects = createQuery(() => queries.projects());
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
  let leashMenu = $state<HTMLDetailsElement | null>(null);
  let recapElement = $state<HTMLElement | null>(null);
  let recapExpanded = $state(false);
  let recapCanExpand = $state(false);

  /** How far open this page's conversation is.
   *
   * The Ticket status owns the opening state. A paired Ticket opens full. Every other
   * status opens at rest. The person can still move the conversation within that state
   * until the Ticket status changes again.
   */
  let conversationState = $state<ConversationState>("rest");

  $effect(() => {
    const status = ticket.data?.ticket_status;
    if (status === undefined) return;
    conversationState = status === "paired" ? "opened" : "rest";
  });

  let projectOptions = $derived([
    { value: "", label: "No project" },
    ...(projects.data?.projects || []).map((project) => ({ value: project.id, label: project.name }))
  ]);

  $effect(() => {
    const recap = ticket.data?.recap;
    const node = recapElement;
    if (!node) return;
    void recap;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(node).lineHeight);
      const maxHeight = Number.isFinite(lineHeight) ? lineHeight * 3 : node.clientHeight;
      recapCanExpand = node.scrollHeight > maxHeight + 1;
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    const frame = requestAnimationFrame(measure);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  });

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

  function closeLeash(): void {
    if (leashMenu) leashMenu.open = false;
  }

  async function updateScope(body: Record<string, unknown>): Promise<void> {
    headerError = null;
    try {
      await saveScope(body);
      closeLeash();
    } catch (err) {
      headerError = err;
    }
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

  function replaceNote(field: string, note: string): Promise<unknown> {
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

  async function copyFromLeash(): Promise<void> {
    await copyTicket();
    closeLeash();
  }

  async function takeoverFromLeash(detail: TicketDetail): Promise<void> {
    await takeover(detail);
    closeLeash();
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
      <main class="ticket-doc">
        <header class="ticket-head">
          <div class="ticket-identity" data-ticket-identity>
            <TicketPriorityControl
              priority={detail.priority}
              onChange={(priority) => {
                if (priority !== detail.priority) void patch({ priority });
              }}
            />
            <span class="ticket-identity-group">
              <span class="ticket-identity-separator" aria-hidden="true">·</span>
              <span
                class="ticket-identity-fact"
                class:ticket-identity-add={!detail.project_id}
                data-project-control
              >
                {#if detail.project_id}
                  {detail.project}
                {:else}
                  No project
                {/if}
                {#if !detail.sprint_item_id}
                  <select
                    aria-label={detail.project_id ? "Ticket project" : "Add ticket project"}
                    value={detail.project_id || ""}
                    onchange={(event) => void patch({ project_id: event.currentTarget.value || null })}
                  >
                    {#each projectOptions as option}
                      <option value={option.value}>{option.label}</option>
                    {/each}
                  </select>
                {/if}
              </span>
            </span>
            {#if lc}
              <span class="ticket-identity-group">
                <span class="ticket-identity-separator" aria-hidden="true">·</span>
                <span class="ticket-identity-fact" data-ticket-worker-name>{lc.workerTypeLabel}</span>
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
            <div
              bind:this={recapElement}
              class="ticket-recap"
              class:ticket-recap--clamped={recapCanExpand && !recapExpanded}
              data-recap
            >
              <InlineEdit
                value={detail.recap}
                markdown
                multiline
                placeholder="+ add orientation"
                onSave={(raw) =>
                  mutateJson(`/api/tickets/${stableId}/recap`, {
                    method: "PUT",
                    body: { body: raw }
                  })}
              />
            </div>
            {#if recapCanExpand}
              <button
                type="button"
                class="ticket-recap-more"
                data-recap-toggle
                onclick={() => (recapExpanded = !recapExpanded)}
              >{recapExpanded ? "Show less" : "Show more"}</button>
            {/if}
          </div>
          <div class="ticket-operating">
            {#if detail.stage !== "done" && detail.stage !== "needs_kickoff"}
              <details class="ticket-leash" bind:this={leashMenu} data-leash>
                <summary
                  class="ticket-leash-face"
                  class:ticket-leash-face--held={userOwnsCurrentStage(detail)}
                  data-leash-face
                >
                  {#if userOwnsCurrentStage(detail)}
                    <span class="ticket-leash-value">you hold {stageLabel(detail.stage)}</span>,
                  {/if}
                  approved until
                  <span class="ticket-leash-value" data-leash-ceiling>{stageLabel(detail.ceiling)}</span>,
                  then <span class="ticket-leash-value" data-leash-cap>{detail.at_cap === "propose" ? "continue" : "stop"}</span>
                  <span class="disclosure-chev" aria-hidden="true"></span>
                </summary>
                <div class="ticket-leash-menu" role="menu">
                  <select
                    class="ticket-leash-select"
                    data-scope-ceiling
                    aria-label="Approved until stage"
                    value={detail.ceiling}
                    onchange={(event) => void updateScope({ ceiling: event.currentTarget.value, at_cap: detail.at_cap })}
                  >
                    {#each ceilingOptionsFor(lc, detail.stage) as option}
                      <option value={option.value}>{option.label}</option>
                    {/each}
                  </select>
                  <div class="ticket-leash-rule"></div>
                  <div data-scope-atcap>
                    <select
                      class="ticket-leash-select"
                      aria-label="At the ceiling"
                      value={detail.at_cap}
                      onchange={(event) => void updateScope({ ceiling: detail.ceiling, at_cap: event.currentTarget.value })}
                    >
                      <option value="stop">then stop</option>
                      <option value="propose">then continue</option>
                    </select>
                  </div>
                  <div class="ticket-leash-rule"></div>
                  {#if detail.stage !== "needs_kickoff" && detail.stage !== "done"}
                    <button
                      type="button"
                      class="ticket-leash-option"
                      data-ticket-takeover-toggle=""
                      onclick={() => void takeoverFromLeash(detail)}
                    >
                      <span class="ticket-leash-option-mark"></span>
                      {userOwnsCurrentStage(detail) ? "Release" : "Take over"}
                    </button>
                  {/if}
                  <button
                    type="button"
                    class="ticket-leash-option"
                    data-copy=""
                    onclick={() => void copyFromLeash()}
                  >
                    <span class="ticket-leash-option-mark"></span>
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
              </details>
            {/if}
            {#if detail.stage === "needs_kickoff" || detail.stage === "done"}
              <button
                type="button"
                class="ticket-operating-copy"
                data-copy=""
                onclick={() => void copyTicket()}
              >{copied ? "Copied" : "Copy"}</button>
            {/if}
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
            {#if lc}
              {@const settledFields = lc.fieldIds.filter(
                (name) => fieldStageVisualStateFor(lc, detail, name) === "completed"
              )}
              {#if settledFields.length}
                <details class="stage-fold" data-stage-fold>
                  <summary class="stage-fold-summary" data-stage-fold-open>
                    <StageMark state="completed" />
                    <span class="stage-fold-names">{settledFields.join(" · ")}</span>
                    <span class="disclosure-chev" aria-hidden="true"></span>
                  </summary>
                  <div class="stage-fold-rows">
                    {#each settledFields as name}
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
                        onReplaceNote={(raw) => replaceNote(name, raw)}
                        onSaveValue={(raw) => saveValue(name, raw)}
                      />
                    {/each}
                    <button
                      type="button"
                      class="stage-fold-close"
                      data-stage-fold-close
                      onclick={(event) => {
                        const fold = event.currentTarget.closest("details");
                        if (fold) fold.open = false;
                      }}
                    >Fold settled stages</button>
                  </div>
                </details>
              {/if}
              {#each lc.fieldIds.filter((name) => !settledFields.includes(name)) as name}
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
                  onReplaceNote={(raw) => replaceNote(name, raw)}
                  onSaveValue={(raw) => saveValue(name, raw)}
                />
              {/each}
            {/if}
          </div>
        </div>
      </main>
      <div
        class="ticket-conversation-layer"
        data-conversation-layer-host
      >
        <div class="ticket-conversation-column">
          <LiveConversation
            bind:conversationState
            conversationId={detail.conversation_id}
            ticketId={detail.id}
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
