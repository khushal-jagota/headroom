<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource, ResourceHandle } from "../lib/resources";
  import { fieldStageVisualState, gatingField } from "../lib/ui";
  import type { AnyRecord, QueueEntry, QueuesResponse, TicketDetail } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import ContentDisclosure from "../components/ContentDisclosure.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  const queues = resource<QueuesResponse>("queues", (signal) =>
    fetchJson("/api/queues", { signal })
  );

  let skipped = $state<Record<string, boolean>>({});
  let detailResource = $state<ResourceHandle<AnyRecord> | null>(null);
  let detailError = $state<unknown>(null);
  const staleRefreshRequests = new Set<string>();

  function entryKey(entry: QueueEntry): string {
    return `${entry.entity_id}:${entry.kind}`;
  }

  let entries = $derived(queues.data?.approvals || []);
  let runningAgentCount = $derived(queues.data?.running_agents ?? 0);
  let currentEntry = $derived.by<QueueEntry | null>(() => {
    if (!entries.length) return null;
    const live = entries.filter((entry) => !skipped[entryKey(entry)]);
    return live[0] || null;
  });

  function runningAgentsText(count: number): string {
    return `${count} ${count === 1 ? "agent" : "agents"} in progress`;
  }

  $effect(() => {
    const entry = currentEntry;
    detailError = null;
    const handle = entry
      ? resource<AnyRecord>(
          `${entry.entity_type === "ticket" ? "ticket" : "item"}:${entry.entity_id}`,
          (signal) =>
            fetchJson(
              `${entry.entity_type === "ticket" ? "/api/tickets/" : "/api/items/"}${entry.entity_id}`,
              { signal }
            )
        )
      : null;
    detailResource = handle;
    return () => handle?.dispose();
  });

  function isStale(entry: QueueEntry, detail: AnyRecord): boolean {
    if (entry.kind === "review") return detail.state !== "needs_review";
    if (entry.kind === "status") return !detail.status_proposal;
    if (!detail.fields?.[entry.kind]?.proposal) return true;
    return gatingField(String(detail.state)) !== entry.kind;
  }

  $effect(() => {
    const entry = currentEntry;
    const detail = detailResource?.data;
    if (entry && detail && isStale(entry, detail)) {
      const key = entryKey(entry);
      if (!staleRefreshRequests.has(key)) {
        staleRefreshRequests.add(key);
        void queues.refresh().catch(() => undefined);
      }
    }
    if (detailResource?.error) detailError = detailResource.error;
  });

  function skip(entry: QueueEntry): void {
    skipped = { ...skipped, [entryKey(entry)]: true };
  }

  async function refreshQueuesAfter<T>(operation: Promise<T>): Promise<T> {
    const result = await operation;
    await queues.refresh().catch(() => undefined);
    return result;
  }

  function accept(entry: QueueEntry, payload: Record<string, unknown>): Promise<unknown> {
    return refreshQueuesAfter(mutateJson(
      `/api/tickets/${entry.entity_id}/accept/${entry.kind}`,
      { method: "POST", body: payload },
      ["queues", `ticket:${entry.entity_id}`, "board", "sprint:current"]
    ));
  }

  function approve(entry: QueueEntry): Promise<unknown> {
    return refreshQueuesAfter(mutateJson(
      `/api/tickets/${entry.entity_id}/approve`,
      { method: "POST", body: {} },
      ["queues", `ticket:${entry.entity_id}`, "board", "sprint:current"]
    ));
  }

  function acceptStatus(entry: QueueEntry): Promise<unknown> {
    return refreshQueuesAfter(mutateJson(
      `/api/items/${entry.entity_id}/accept-status`,
      { method: "POST", body: {} },
      ["queues", `item:${entry.entity_id}`, "items:backlog", "sprint:current", "board"]
    ));
  }

  onDestroy(() => {
    queues.dispose();
    detailResource?.dispose();
  });
</script>

<section class="review-screen" data-screen="review">
  {#if queues.error}
    <ErrorLine error={queues.error} />
  {:else if queues.loading && !queues.data}
    <div class="quiet-line">Loading review...</div>
  {:else if !entries.length}
    <div class="review-empty-state" data-review-empty>
      <div class="review-empty-mark" aria-hidden="true"><span></span></div>
      <div class="review-empty-text">There is nothing to review right now.</div>
      <div class="review-empty-meta">{runningAgentsText(runningAgentCount)}</div>
    </div>
  {:else if currentEntry}
    {@const entry = currentEntry}
    {#if detailError}
      <div>
        <ErrorLine error={detailError} />
        <div class="quiet-line">{entry.title}</div>
        <div class="review-card-actions">
          <button type="button" class="button" data-skip onclick={() => skip(entry)}>Skip</button>
          {#if entry.entity_type === "ticket"}
            <a data-open-ticket href={`#/ticket/${entry.entity_id}`}>open ticket</a>
          {/if}
        </div>
      </div>
    {:else if detailResource?.loading && !detailResource.data}
      <div class="quiet-line">Loading approval...</div>
    {:else if detailResource?.data}
      {@const detail = detailResource.data as TicketDetail & AnyRecord}
      {#if isStale(entry, detail)}
        <div class="quiet-line">Loading approval...</div>
      {:else}
        <div
          class="modern-review-content"
          data-review-card
          data-entity-id={entry.entity_id}
          data-kind={entry.kind}
          data-field={["success", "approach", "plan", "result"].includes(entry.kind) ? entry.kind : undefined}
        >
          {#if entry.entity_type === "ticket"}
            <a href={`#/ticket/${entry.entity_id}`} class="review-ticket-title">
              {entry.title}
            </a>
          {:else}
            <h2 class="review-ticket-title" style="pointer-events: none;">{entry.title}</h2>
          {/if}

          {#if ["success", "approach", "plan", "result"].includes(entry.kind)}
            <TicketStageSection
              variant="review"
              name={entry.kind}
              slot={detail.fields[entry.kind]}
              ticketState={detail.state}
              ceiling={detail.ceiling}
              stageState={fieldStageVisualState(detail, entry.kind)}
              recap={detail.recap}
              showRecap
              onAccept={(payload) => accept(entry, payload)}
            />
          {:else if entry.kind === "review"}
            <TicketStageSection
              variant="review"
              name="result"
              slot={detail.fields.result}
              ticketState={detail.state}
              ceiling={detail.ceiling}
              stageState={fieldStageVisualState(detail, "result")}
              recap={detail.recap}
              showRecap
              onAccept={() => approve(entry)}
              onApproveResult={() => approve(entry)}
            />
          {:else if entry.kind === "status"}
            <div class="approval approval--review">
              <div class="approval-proposal-shell review-status-proposal">
                <ContentDisclosure title="Status" section="proposal">
                  <div class="review-status-change">
                    <span class="pill">{detail.status}</span>
                    <span class="review-status-arrow" aria-hidden="true">→</span>
                    <span class="pill review-status-target">{detail.status_proposal?.to_status || ""}</span>
                  </div>
                </ContentDisclosure>
                <div class="approval-actions">
                  <div class="approval-control-group">
                    <button type="button" class="approval-approve" data-accept data-accept-status onclick={() => void acceptStatus(entry)}>Approve</button>
                  </div>
                </div>
              </div>
            </div>
          {/if}

          <div class="review-outside-actions">
            <button type="button" class="approval-skip" data-skip onclick={() => skip(entry)}>Skip</button>
            {#if entry.entity_type === "ticket"}
              <a class="review-open-ticket" data-open-ticket href={`#/ticket/${entry.entity_id}`}>Open ticket</a>
            {/if}
          </div>
        </div>
      {/if}
    {/if}
  {:else}
    <div class="review-empty-state" data-review-empty>
      <div class="review-empty-mark" aria-hidden="true"><span></span></div>
      <div class="review-empty-text">There is nothing to review right now.</div>
      <div class="review-empty-meta">{runningAgentsText(runningAgentCount)}</div>
    </div>
  {/if}
</section>
