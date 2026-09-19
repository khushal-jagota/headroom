<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { labelize, stageLabel } from "../lib/ui";
  import {
    ceilingOptionsFor,
    fieldStageVisualStateFor,
    gatingFieldFor,
    lifecycleFor
  } from "../lib/lifecycle";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../lib/types";
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import { initialTicketConversationState } from "../lib/conversation/ticketConversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import WorkerConfigurationSetup from "../components/WorkerConfigurationSetup.svelte";
  import ClampedText from "../components/ClampedText.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import StageMark from "../components/StageMark.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";
  import TicketPriorityControl from "../components/TicketPriorityControl.svelte";
  import TicketVerdict from "../components/TicketVerdict.svelte";
  import TicketTroubleNotes from "../components/TicketTroubleNotes.svelte";
  import ArtifactPreview from "../components/ArtifactPreview.svelte";
  import ArtifactStrip from "../components/ArtifactStrip.svelte";
  import { ticketArtifactStripItems } from "../lib/artifactStrip";
  import { isPlainLinkClick } from "../lib/linkClick";
  import {
    targetFromPreviewHref,
    type ManagedFileTarget
  } from "../lib/filePreview";

  let {
    id,
    // The artifact this screen is showing, when the host keeps that in its address, and
    // the way to write it there. The Workspace supplies both; without them, the screen
    // holds the value itself.
    openFile = null,
    onOpenFile = null
  }: {
    id: string;
    openFile?: ManagedFileTarget | null;
    onOpenFile?: ((file: ManagedFileTarget | null) => void) | null;
  } = $props();
  const stableId = untrack(() => id);

  const ticket = createQuery(() => queries.ticket(stableId));
  // What a conversation for this Ticket's worker would start on, asked of the server
  // because the server is what resolves it: the Worker type's launch defaults and
  // whatever this Ticket last ran on, through the same code that will create it.
  const conversationStartValues = createQuery(() =>
    queries.ticketConversationStartValues(stableId)
  );
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
  let conversationBackends = $state<readonly BackendSnapshot[]>([]);
  let leashMenu = $state<HTMLDetailsElement | null>(null);

  /** How far open this page's conversation is.
   *
   * The state a conversation opens in belongs to the page that shows it. A user-owned Ticket
   * opens full, and every other Ticket opens at rest. The person moves it from there and
   * the conversation writes back here when they do.
   */
  let conversationState = $state<ConversationState>("rest");
  /** Seed the conversation once from the status at the start of this Ticket visit.
   *
   * A live status change is not a new visit. After the seed, only the person's expand,
   * collapse, and dismissal actions change this state. The route remounts this component
   * for another Ticket, so that Ticket receives its own seed.
   */
  let seededConversationStateFromStatus = false;
  $effect(() => {
    const ownership = ticket.data && lc ? lc.stageOwnershipMode[ticket.data.stage] : undefined;
    if (
      !ticket.isFetchedAfterMount ||
      !ticket.isSuccess ||
      ownership === undefined ||
      seededConversationStateFromStatus
    ) {
      return;
    }
    seededConversationStateFromStatus = true;
    conversationState = initialTicketConversationState(ownership);
  });

  /** A click outside the conversation dismisses it to rest. */
  function dismissConversationToRest(): void {
    if (conversationState !== "rest") conversationState = "rest";
  }

  /** A press beside the centered conversation card dismisses it to rest. */
  function dismissConversationOnAPressBesideTheCard(event: MouseEvent): void {
    const pressed = event.target;
    if (!(pressed instanceof Element)) return;
    if (pressed.closest("[data-conversation-pane]") !== null) return;
    dismissConversationToRest();
  }

  /** The artifact this screen is showing, when the host does not keep it in an address. */
  let heldFile = $state<ManagedFileTarget | null>(null);
  let shownFile = $derived(onOpenFile ? openFile : heldFile);
  let artifactReloadSignal = $state(0);

  /** Show a file on this screen, or close the one it is showing.
   *
   * The Ticket stays where it is underneath, so the person who came to read an artifact
   * has not left the work it belongs to. An opened conversation is the whole page, and
   * an artifact opened behind it would be an artifact nobody can see, so the
   * conversation steps back one to let it through.
   */
  function showFile(file: ManagedFileTarget | null): void {
    if (file && !shownFile) {
      whatHadFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    if (onOpenFile) onOpenFile(file);
    else heldFile = file;
    if (file && conversationState === "opened") conversationState = "peeked";
    if (!file) {
      const restoreFocus = whatHadFocus;
      whatHadFocus = null;
      queueMicrotask(() => restoreFocus?.focus());
    }
  }

  /** The artifact takes focus when it opens, and gives it back when it closes.
   *
   * The reader is now reading the artifact, so that is where the keyboard should be. It
   * is also what makes Escape reach this screen: a Ticket field is an editable surface
   * and Escape belongs to the editor while the caret is in one, so an artifact opened
   * from a link inside a field must move the focus out of it.
   */
  let artifactElement = $state<HTMLElement | null>(null);
  let whatHadFocus: HTMLElement | null = null;
  $effect(() => {
    if (shownFile && artifactElement) artifactElement.focus();
  });

  /** A click on a link to a managed file opens the file here.
   *
   * Every "Open plan.md" the shared preview writes points at the `#/preview` address, in
   * a Ticket field, in a note, and in the conversation alike. On this screen that address
   * is not somewhere to go: the file is opened over the Ticket's own document instead.
   * A click asking for a new tab or a new window is left alone, and so is every link that
   * names anything else — a dev server among them, which is a page and not a file.
   */
  function openManagedFileInPlace(event: MouseEvent): void {
    if (event.defaultPrevented) return;
    const anchor = (event.target as Element | null)?.closest?.("a[href]");
    if (!anchor) return;
    const link = { href: anchor.getAttribute("href"), target: anchor.getAttribute("target") };
    if (!isPlainLinkClick(link, event)) return;
    const file = targetFromPreviewHref(link.href);
    if (!file) return;
    event.preventDefault();
    showFile(file);
  }

  /** Escape closes the artifact, and belongs to it before anything else on the page.
   *
   * The conversation steps back a state on Escape too, so the press is claimed here to
   * keep one press to one thing.
   */
  function closeFileOnEscape(event: KeyboardEvent): void {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (!shownFile) return;
    event.preventDefault();
    showFile(null);
  }

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
    return mutateJson(`/api/tickets/${stableId}`, { method: "PATCH", body });
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

  function saveValue(field: string, body: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}`, {
      method: "PATCH",
      body: { field_values: { [field]: body } }
    });
  }

  function completeGate(field: string, body: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/complete/${field}`, {
      method: "POST",
      body: { body }
    });
  }

  function saveVerdict(verdict: { rating: number | null; text: string | null }): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/verdict`, {
      method: "PUT",
      body: verdict
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

  function userOwnsCurrentStage(detail: TicketDetail): boolean {
    return lc?.stageOwnershipMode[detail.stage] === "user";
  }

  function currentStageRunLabel(detail: TicketDetail): string | null {
    if (detail.blocked || detail.ticket_status === "blocked") return null;
    if (detail.awaiting_approval) return "awaiting approval";
    if (detail.assigned) {
      return "you're on it";
    }
    return null;
  }

  function conversationWorkerTypeLabel(detail: TicketDetail): string {
    return lc?.workerTypeLabel ?? labelize(detail.worker_type);
  }

  function conversationEmployeeLabel(detail: TicketDetail): string {
    const workerTypeLabel = conversationWorkerTypeLabel(detail);
    return /worker$/i.test(workerTypeLabel) ? workerTypeLabel : `${workerTypeLabel} worker`;
  }

  // The Kickoff approval card still carries Worker configuration while that
  // choice is editable. Direct blockers belong to the Ticket itself, so they
  // always stay in the masthead instead of moving into a stage card.
  let kickoffCardShowsContextRow = $derived(
    gatingFieldFor(lc, ticket.data?.stage ?? "") === "kickoff" &&
      ticket.data?.pending_proposal?.field === "kickoff" &&
      Boolean(ticket.data?.employee_configuration_editable)
  );

  async function removeBlocker(blockerTicketId: string): Promise<void> {
    headerError = null;
    try {
      await mutateJson(
        `/api/collections/blockers/${encodeURIComponent(stableId)}/${encodeURIComponent(blockerTicketId)}`,
        { method: "DELETE" }
      );
    } catch (err) {
      headerError = err;
    }
  }
</script>

<svelte:window onkeydown={closeFileOnEscape} />

<!-- The catcher for links to managed files sits on the whole screen, so a file opens in
     place wherever it was written: a Ticket field, the recap, or the conversation. -->
<!-- The links inside keep their own keyboard behaviour: Enter on a link raises this same
     event, so the catcher needs no key handling of its own. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<section
  class="ticket-screen"
  data-screen="ticket"
  data-ticket-id={stableId}
  data-stage={ticket.data?.stage}
  onclick={openManagedFileInPlace}
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
      <div class="ticket-reading">
      <main class="ticket-doc" onclickcapture={dismissConversationToRest}>
        <header class="ticket-head">
          <div class="ticket-identity" data-ticket-identity>
            <TicketPriorityControl
              priority={detail.priority}
              onChange={(priority) => {
                if (priority !== detail.priority) void patch({ priority });
              }}
            />
            <span class="ticket-identity-group" data-ticket-placement>
              <span class="ticket-identity-separator" aria-hidden="true">·</span>
              <span class="ticket-identity-fact" data-project-fact>
                {detail.project || "No project"}
              </span>
              {#if detail.sprint_item_id}
                <span class="ticket-identity-separator" aria-hidden="true">·</span>
                <span class="ticket-identity-fact" data-outcome-fact>
                  {detail.resolved_priority_anchors.sprint_item?.title || "Sprint Item"}
                </span>
              {/if}
            </span>
            {#if lc}
              <span class="ticket-identity-group">
                <span class="ticket-identity-separator" aria-hidden="true">·</span>
                <span class="ticket-identity-fact" data-ticket-worker-name>{lc.workerTypeLabel}</span>
              </span>
            {/if}
          </div>
          <div class="ticket-title-row">
            <div class="ticket-title" data-feedback-page-title>
              <InlineEdit
                value={detail.title}
                placeholder="Untitled"
                onSave={(raw) => patch({ title: raw })}
              />
            </div>
            <ClampedText
              contentKey={detail.recap}
              moreControlAttributes={{ "data-recap-toggle": "" }}
              data-recap
            >
              <InlineEdit
                value={detail.recap}
                markdown
                multiline
                placeholder="+ add orientation"
                onSave={(raw) =>
                  mutateJson(`/api/tickets/${stableId}`, {
                    method: "PATCH",
                    body: { recap: raw }
                  })}
              />
            </ClampedText>
          </div>
          <div class="ticket-operating">
            {#if detail.stage !== "done" && detail.stage !== "needs_kickoff" && detail.pending_proposal === null}
              <details class="ticket-leash" bind:this={leashMenu} data-leash>
                <summary
                  class="ticket-leash-face"
                  data-leash-face
                >
                  approved until
                  <span class="ticket-leash-value" data-leash-ceiling>{stageLabel(detail.ceiling)}</span>
                  <span class="disclosure-chev" aria-hidden="true"></span>
                </summary>
                <div class="ticket-leash-menu" role="menu">
                  <select
                    class="ticket-leash-select"
                    data-scope-ceiling
                    aria-label="Approved until stage"
                    value={detail.ceiling}
                    onchange={(event) => void updateScope({ ceiling: event.currentTarget.value })}
                  >
                    {#each ceilingOptionsFor(lc, detail.stage) as option}
                      <option value={option.value}>{option.label}</option>
                    {/each}
                  </select>
                </div>
              </details>
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
          {#if headerError}<ErrorLine error={headerError} />{/if}
        </header>

        <div class="ticket-col">
          {#if lc}
            <ArtifactStrip items={ticketArtifactStripItems(lc.fieldIds, detail.field_values, detail.pending_proposal)} />
          {/if}
          <TicketVerdict stage={detail.stage} verdict={detail.verdict} onSave={saveVerdict} />
          <TicketTroubleNotes notes={detail.trouble_notes} />
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
                      {@const stageState = fieldStageVisualStateFor(lc, detail, name)}
                      <TicketStageSection
                        {name}
                        value={detail.field_values[name] ?? ""}
                        pendingProposal={detail.pending_proposal?.field === name ? detail.pending_proposal : null}
                        {stageState}
                        lifecycle={lc}
                        ticketStage={detail.stage}
                        ceiling={detail.ceiling}
                        emptyText={emptyTicketFieldText}
                        editableCurrentValue={userOwnsCurrentStage(detail)}
                        runLabel={stageState.startsWith("current-") ? currentStageRunLabel(detail) : null}
                        runLabelAttention={stageState === "current-awaiting-approval"}
                        contextRow={name === "kickoff" && kickoffCardShowsContextRow
                          ? kickoffContextRow
                          : undefined}
                        onAccept={(payload) => acceptField(name, payload)}
                        onSaveValue={(raw) => saveValue(name, raw)}
                        onCompleteGate={(raw) => completeGate(name, raw)}
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
                {@const stageState = fieldStageVisualStateFor(lc, detail, name)}
                <TicketStageSection
                  {name}
                  value={detail.field_values[name] ?? ""}
                  pendingProposal={detail.pending_proposal?.field === name ? detail.pending_proposal : null}
                  {stageState}
                  lifecycle={lc}
                  ticketStage={detail.stage}
                  ceiling={detail.ceiling}
                  emptyText={emptyTicketFieldText}
                  editableCurrentValue={userOwnsCurrentStage(detail)}
                  runLabel={stageState.startsWith("current-") ? currentStageRunLabel(detail) : null}
                  runLabelAttention={stageState === "current-awaiting-approval"}
                  contextRow={name === "kickoff" && kickoffCardShowsContextRow
                    ? kickoffContextRow
                    : undefined}
                  onAccept={(payload) => acceptField(name, payload)}
                  onSaveValue={(raw) => saveValue(name, raw)}
                  onCompleteGate={(raw) => completeGate(name, raw)}
                />
              {/each}
            {/if}
          </div>
        </div>
      </main>
      {#if shownFile}
        <ArtifactPreview
          target={shownFile}
          reloadSignal={artifactReloadSignal}
          bind:element={artifactElement}
          onRefresh={() => artifactReloadSignal += 1}
          onClose={() => showFile(null)}
        />
      {/if}
      </div>
      <div
        class="conversation-layer"
        data-conversation-layer-host
        onclickcapture={dismissConversationOnAPressBesideTheCard}
      >
        <div class="conversation-column">
          <LiveConversation
            bind:conversationState
            conversationId={detail.conversation_id}
            persistenceKey={`owner:ticket:${detail.id}`}
            label={conversationWorkerTypeLabel(detail)}
            composerPlaceholder={`Message ${conversationEmployeeLabel(detail)}...`}
            bind:backends={conversationBackends}
            startValues={conversationStartValues.data ?? null}
            senderLabel="owner"
            sendMessage={sendToTicketWorker}
            onNewConversation={resetTicketConversation}
          />
        </div>
      </div>
    </div>
    {/if}
  </ResourceState>
</section>
