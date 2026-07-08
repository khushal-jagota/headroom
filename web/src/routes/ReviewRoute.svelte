<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource, ResourceHandle } from "../lib/resources";
  import { advanceTarget, gatingField } from "../lib/ui";
  import type { AnyRecord, QueueEntry, QueuesResponse, TicketDetail } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ProposalCard from "../components/ProposalCard.svelte";

  const queues = resource<QueuesResponse>("queues", (signal) =>
    fetchJson("/api/queues", { signal })
  );

  let skipped = $state<Record<string, boolean>>({});
  let detailResource = $state<ResourceHandle<AnyRecord> | null>(null);
  let detailError = $state<unknown>(null);

  function entryKey(entry: QueueEntry): string {
    return `${entry.entity_id}:${entry.kind}`;
  }

  let entries = $derived(queues.data?.approvals || []);
  let currentEntry = $derived.by<QueueEntry | null>(() => {
    if (!entries.length) return null;
    const live = entries.filter((entry) => !skipped[entryKey(entry)]);
    return live[0] || entries[0] || null;
  });

  $effect(() => {
    if (entries.length && entries.every((entry) => skipped[entryKey(entry)])) {
      skipped = {};
    }
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
    if (entry.kind === "status") return !detail.status_proposal;
    if (!detail.fields?.[entry.kind]?.proposal) return true;
    return gatingField(String(detail.state)) !== entry.kind;
  }

  $effect(() => {
    if (currentEntry && detailResource?.data && isStale(currentEntry, detailResource.data)) {
      skipped = { ...skipped, [entryKey(currentEntry)]: true };
    }
    if (detailResource?.error) detailError = detailResource.error;
  });

  function skip(entry: QueueEntry): void {
    skipped = { ...skipped, [entryKey(entry)]: true };
  }

  function accept(entry: QueueEntry, payload: Record<string, unknown>): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${entry.entity_id}/accept/${entry.kind}`,
      { method: "POST", body: payload },
      ["queues", `ticket:${entry.entity_id}`, "board", "sprint:current"]
    );
  }

  function approve(entry: QueueEntry): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${entry.entity_id}/approve`,
      { method: "POST", body: {} },
      ["queues", `ticket:${entry.entity_id}`, "board", "sprint:current"]
    );
  }

  function acceptStatus(entry: QueueEntry): Promise<unknown> {
    return mutateJson(
      `/api/items/${entry.entity_id}/accept-status`,
      { method: "POST", body: {} },
      ["queues", `item:${entry.entity_id}`, "items:backlog", "sprint:current", "board"]
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
      <section
        class="panel"
        data-review-card
        data-entity-id={entry.entity_id}
        data-kind={entry.kind}
        data-field={["success", "approach", "plan", "result"].includes(entry.kind) ? entry.kind : undefined}
      >
        <h2 class="panel-title">{entry.title}</h2>
        <div class="panel-body">
          {#if ["success", "approach", "plan", "result"].includes(entry.kind)}
            <div class="review-card-head">
              <Chip variant="state" value={detail.state} />
              <Chip variant="pending-proposal" />
            </div>
            <ProposalCard
              proposal={detail.fields[entry.kind].proposal || { body: "", proposed_by: "" }}
              requireScope
              newState={advanceTarget(detail.state, detail.ceiling)}
              onAccept={(payload) => accept(entry, payload)}
            />
          {:else if entry.kind === "review"}
            <div class="review-card-head"><Chip variant="state" value={detail.state} /></div>
            <MarkdownBlock text={detail.fields.result.value} />
            {#if detail.fields.result.notes}
              <div class="review-note"><MarkdownBlock text={detail.fields.result.notes} /></div>
            {/if}
            <div class="review-card-actions">
              <button type="button" class="button button--primary" data-approve onclick={() => void approve(entry)}>Approve</button>
              <button type="button" class="button" data-skip onclick={() => skip(entry)}>Skip</button>
              <a class="review-open" data-open-ticket href={`#/ticket/${entry.entity_id}`}>open ticket</a>
            </div>
          {:else if entry.kind === "status"}
            <div class="review-card-head">
              <Chip value={detail.status} />
              <Chip value={`→ ${detail.status_proposal?.to_status || ""}`} />
            </div>
            {#if detail.status_proposal?.note}
              <div class="review-note">{detail.status_proposal.note}</div>
            {/if}
            <div class="review-card-actions">
              <button type="button" class="button button--primary" data-accept-status onclick={() => void acceptStatus(entry)}>Accept</button>
              <button type="button" class="button" data-skip onclick={() => skip(entry)}>Skip</button>
            </div>
          {/if}
        </div>
      </section>
    {/if}
  {/if}
</section>
