<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource, ResourceHandle } from "../lib/resources";
  import { advanceTarget, gatingField } from "../lib/ui";
  import type { AnyRecord, QueueEntry, QueuesResponse, TicketDetail } from "../lib/types";
  import ApprovalBlock from "../components/ApprovalBlock.svelte";
  import Chip from "../components/Chip.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";

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
  let currentEntry = $derived.by<QueueEntry | null>(() => {
    if (!entries.length) return null;
    const live = entries.filter((entry) => !skipped[entryKey(entry)]);
    return live[0] || null;
  });

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

  function saveNote(ticketId: string, field: string, note: string): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${ticketId}/notes/${field}`,
      { method: "PUT", body: { note } },
      ["queues", `ticket:${ticketId}`, "board", "sprint:current"]
    );
  }

  function saveValue(ticketId: string, field: string, body: string): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${ticketId}/value/${field}`,
      { method: "PUT", body: { body } },
      ["queues", `ticket:${ticketId}`, "board", "sprint:current"]
    );
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
    <div class="quiet-line" data-review-empty>nothing waiting</div>
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
          <!-- Simple Ticket Title linked to the ticket page -->
          {#if entry.entity_type === "ticket"}
            <a href={`#/ticket/${entry.entity_id}`} class="review-ticket-title">
              {entry.title}
            </a>
          {:else}
            <h2 class="review-ticket-title" style="pointer-events: none;">{entry.title}</h2>
          {/if}

          <!-- Recap: Plain Text Section -->
          {#if detail.recap}
            <div style="margin-bottom: var(--space-4);">
              <div style="font-size: var(--type-xs); font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faintest); margin-bottom: var(--space-1);">Recap</div>
              <div style="font-size: var(--type-md); color: var(--text-default); line-height: 1.62;">
                <MarkdownBlock text={detail.recap} />
              </div>
            </div>
          {/if}

          <!-- Note: Plain Text Section -->
          {#if ["success", "approach", "plan", "result"].includes(entry.kind) && detail.fields?.[entry.kind]?.notes}
            <div style="margin-bottom: var(--space-5);">
              <div style="font-size: var(--type-xs); font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faintest); margin-bottom: var(--space-1);">Note</div>
              <div style="font-size: var(--type-sm); color: var(--text-muted); line-height: 1.55;">
                <MarkdownBlock text={detail.fields[entry.kind].notes} />
              </div>
            </div>
          {:else if entry.kind === "review" && detail.fields?.result?.notes}
            <div style="margin-bottom: var(--space-5);">
              <div style="font-size: var(--type-xs); font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faintest); margin-bottom: var(--space-1);">Note</div>
              <div style="font-size: var(--type-sm); color: var(--text-muted); line-height: 1.55;">
                <MarkdownBlock text={detail.fields.result.notes} />
              </div>
            </div>
          {/if}

          <!-- The Proposed Item (Recessed/Sunken Block) -->
          {#if ["success", "approach", "plan", "result"].includes(entry.kind)}
            <div class="proposed-recessed-block">
              <ApprovalBlock
                mode="gating-pending"
                field={entry.kind}
                whatLabel={entry.kind.replace(/_/g, " ")}
                proposalBody={detail.fields[entry.kind].proposal!.body}
                proposedBy={detail.fields[entry.kind].proposal!.proposed_by}
                newState={advanceTarget(detail.state, detail.ceiling)}
                onApprove={(payload) => accept(entry, payload)}
              >
                {#snippet actions()}
                  <button type="button" class="approval-skip" data-skip onclick={() => skip(entry)}>Skip</button>
                {/snippet}
              </ApprovalBlock>
            </div>
          {:else if entry.kind === "review"}
            <div class="proposed-recessed-block">
              <ApprovalBlock
                mode="needs_review"
                field="result"
                whatLabel="Result"
                proposalBody={detail.fields.result.value}
                onApprove={() => approve(entry)}
              >
                {#snippet actions()}
                  <button type="button" class="approval-skip" data-skip onclick={() => skip(entry)}>Skip</button>
                {/snippet}
              </ApprovalBlock>
            </div>
          {/if}
        </div>
      {/if}
    {/if}
  {:else}
    <div class="quiet-line" data-review-empty>nothing waiting</div>
  {/if}
</section>
