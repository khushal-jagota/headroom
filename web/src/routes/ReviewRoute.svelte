<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource, ResourceHandle } from "../lib/resources";
  import { manifestResource } from "../lib/manifest.svelte";
  import { fieldStageVisualStateFor, gatingFieldFor, lifecycleFor } from "../lib/lifecycle";
  import type { AnyRecord, QueueEntry, QueuesResponse, TicketDetail } from "../lib/types";
  import Button from "../components/Button.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";

  const queues = resource<QueuesResponse>("queues", (signal) =>
    fetchJson("/api/queues", { signal })
  );
  const manifest = manifestResource();

  let skipped = $state<Record<string, boolean>>({});
  let detailResource = $state<ResourceHandle<AnyRecord> | null>(null);
  let detailError = $state<unknown>(null);
  let revisionDraft = $state("");
  let revisionError = $state<unknown>(null);
  let revisionBusy = $state(false);
  let revisionEntryKey = $state<string | null>(null);
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

  // Per-type lifecycle for the current review entry's detail. Null while the
  // manifest or the detail is still loading OR when the detail's ticket_type is
  // absent from a loaded manifest; the markup tells those apart (Codex F3).
  let detailTicketType = $derived(
    typeof detailResource?.data?.ticket_type === "string"
      ? (detailResource.data.ticket_type as string)
      : null
  );
  let lc = $derived(lifecycleFor(manifest.data, detailTicketType));
  let manifestMissingType = $derived(
    Boolean(
      detailTicketType &&
        manifest.data &&
        !manifest.data.types.some((t) => t.type_id === detailTicketType)
    )
  );

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
    const field = approvalField(entry);
    if (!field) return true;
    if (!detail.fields?.[field]?.proposal) return true;
    return gatingFieldFor(lc, String(detail.state)) !== field;
  }

  function approvalField(entry: QueueEntry): string | null {
    if (lc?.fieldIds.includes(entry.kind)) return entry.kind;
    return null;
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

  $effect(() => {
    const key = currentEntry ? entryKey(currentEntry) : null;
    if (key !== revisionEntryKey) {
      revisionEntryKey = key;
      revisionDraft = "";
      revisionError = null;
      revisionBusy = false;
    }
  });

  function skip(entry: QueueEntry): void {
    skipped = { ...skipped, [entryKey(entry)]: true };
  }

  function openTicket(entry: QueueEntry): void {
    if (entry.entity_type !== "ticket") return;
    window.location.hash = `#/ticket/${entry.entity_id}`;
  }

  // Global review shortcuts (approved addition): s = skip, o = open ticket,
  // Cmd/Ctrl+Enter = approve the current ask. They fire only when the keystroke
  // did not originate in an editable control and was not already handled there —
  // InlineEdit's own Cmd/Ctrl+Enter preventDefaults and blurs to <body>, so an
  // activeElement check alone would let that approve by accident.
  function typingTarget(event: KeyboardEvent): boolean {
    const target = event.target as HTMLElement | null;
    if (!target) return false;
    if (target.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName);
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    const entry = currentEntry;
    if (!entry) return;
    // Ignore auto-repeat: holding a key must not skip/approve through the queue.
    if (event.repeat || event.defaultPrevented || typingTarget(event)) return;

    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      const button = document.querySelector<HTMLButtonElement>(
        "[data-review-card] [data-accept]"
      );
      if (button && !button.disabled) {
        event.preventDefault();
        button.click();
      }
      return;
    }

    if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;

    if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      skip(entry);
    } else if (event.key === "o" || event.key === "O") {
      event.preventDefault();
      openTicket(entry);
    }
  }

  $effect(() => {
    window.addEventListener("keydown", onWindowKeydown);
    return () => window.removeEventListener("keydown", onWindowKeydown);
  });

  async function refreshQueuesAfter<T>(operation: Promise<T>): Promise<T> {
    const result = await operation;
    await queues.refresh().catch(() => undefined);
    return result;
  }

  function accept(entry: QueueEntry, payload: Record<string, unknown>): Promise<unknown> {
    const field = approvalField(entry);
    if (!field) return Promise.reject(new Error("review entry is not a ticket field"));
    return refreshQueuesAfter(mutateJson(
      `/api/tickets/${entry.entity_id}/accept/${field}`,
      { method: "POST", body: payload },
      ["queues", `ticket:${entry.entity_id}`, "board", "sprint:current"]
    ));
  }

  function saveTitle(entry: QueueEntry, title: string): Promise<unknown> {
    return mutateJson(
      `/api/tickets/${entry.entity_id}`,
      { method: "PATCH", body: { title } },
      ["queues", `ticket:${entry.entity_id}`, "board", "sprint:current"]
    );
  }


  async function returnForRevision(entry: QueueEntry): Promise<void> {
    const message = revisionDraft.trim();
    if (!message || revisionBusy) return;
    revisionError = null;
    revisionBusy = true;
    try {
      await refreshQueuesAfter(mutateJson(
        `/api/tickets/${entry.entity_id}/return-for-revision`,
        { method: "POST", body: { message } },
        ["queues", `ticket:${entry.entity_id}`, `chat:${entry.entity_id}`, "board", "sprint:current"]
      ));
      revisionDraft = "";
    } catch (err) {
      revisionError = err;
    } finally {
      revisionBusy = false;
    }
  }

  onDestroy(() => {
    queues.dispose();
    detailResource?.dispose();
    manifest.dispose();
  });
</script>

<section class="review-screen" data-screen="review">
  <ResourceState error={queues.error} loading={queues.loading} hasData={Boolean(queues.data)} loadingText="Loading review...">
    {#if !entries.length}
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
          <Button variant="quiet" data-skip="" onclick={() => skip(entry)}>Skip</Button>
          {#if entry.entity_type === "ticket"}
            <a data-open-ticket href={`#/ticket/${entry.entity_id}`}>open ticket</a>
          {/if}
        </div>
      </div>
    {:else if detailResource?.loading && !detailResource.data}
      <div class="quiet-line">Loading approval...</div>
    {:else if detailResource?.data && (manifest.error || manifestMissingType)}
      {@const detail = detailResource.data as TicketDetail & AnyRecord}
      <div data-review-manifest-error>
        <ErrorLine
          error={manifest.error ?? { code: "unknown_ticket_type", message: `no manifest for type "${detail.ticket_type}"` }}
        />
        <div class="review-card-actions">
          <Button variant="quiet" data-skip="" onclick={() => skip(entry)}>Skip</Button>
          {#if entry.entity_type === "ticket"}
            <a data-open-ticket href={`#/ticket/${entry.entity_id}`}>open ticket</a>
          {/if}
        </div>
      </div>
    {:else if detailResource?.data}
      {@const detail = detailResource.data as TicketDetail & AnyRecord}
      {@const field = approvalField(entry)}
      {#if isStale(entry, detail)}
        <div class="quiet-line">Loading approval...</div>
      {:else}
        {#key entryKey(entry)}
          <div
            class="modern-review-content"
            data-review-card
            data-entity-id={entry.entity_id}
            data-kind={entry.kind}
            data-field={field || undefined}
          >
            <div class="review-queue-line review-arrive review-arrive--1">
              <button data-skip="" onclick={() => skip(entry)}>Skip &rsaquo;</button>
              {#if entry.entity_type === "ticket"}
                <a data-open-ticket href={`#/ticket/${entry.entity_id}`}>Open ticket &rsaquo;</a>
              {/if}
            </div>

            {#if entry.entity_type === "ticket"}
              <div class="review-ticket-title review-arrive review-arrive--2">
                <InlineEdit
                  value={detail.title}
                  placeholder="Untitled"
                  onSave={(raw) => saveTitle(entry, raw)}
                />
              </div>
            {:else}
              <h2 class="review-ticket-title review-arrive review-arrive--2" style="pointer-events: none;">{entry.title}</h2>
            {/if}

            <div class="review-arrive review-arrive--3">
              {#if field}
                <TicketStageSection
                  variant="review"
                  name={field}
                  slot={detail.fields[field]}
                  lifecycle={lc}
                  ticketState={detail.state}
                  ceiling={detail.ceiling}
                  stageState={fieldStageVisualStateFor(lc, detail, field)}
                  recap={detail.recap}
                  showRecap
                  onAccept={(payload) => accept(entry, payload)}
                />
              {/if}
            </div>

            {#if entry.entity_type === "ticket" && field !== "kickoff"}
              <div class="review-revise review-arrive review-arrive--4" data-review-revision>
                <div class="review-revision-box">
                  <textarea
                    class="review-revision-input"
                    data-review-revision-input
                    rows="1"
                    placeholder="Or tell the employee what to change..."
                    bind:value={revisionDraft}
                    disabled={revisionBusy}
                    onkeydown={(event) => {
                      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                        event.preventDefault();
                        void returnForRevision(entry);
                      }
                    }}
                  ></textarea>
                  <Button
                    variant="quiet"
                    data-review-revision-send=""
                    disabled={revisionBusy || !revisionDraft.trim()}
                    onclick={() => void returnForRevision(entry)}
                  >
                    Send back
                  </Button>
                </div>
                {#if revisionError}
                  <ErrorLine error={revisionError} />
                {/if}
              </div>
            {/if}
          </div>
        {/key}

        <div class="review-keys">
          <kbd>⌘↩</kbd> approve &nbsp;·&nbsp; <kbd>S</kbd> skip &nbsp;·&nbsp; <kbd>O</kbd> open ticket
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
  </ResourceState>
</section>
