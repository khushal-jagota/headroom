<script lang="ts">
  import { queryClient } from "../lib/queryClient";
  import { cancelUpdate, requestUpdate } from "../lib/updates/api";
  import type { PublishedUpdate, UpdateMode, UpdateStatus } from "../lib/updates/contracts";
  import { activeUpdateRequest, canCancelUpdate, needsActivityConfirmation, usableUpdate } from "../lib/updates/logic/actions";

  let { status, now }: { status: UpdateStatus; now: number } = $props();
  let confirming = $state<PublishedUpdate | null>(null);
  let submitting = $state(false);
  let error = $state<string | null>(null);
  const request = $derived(status.request);
  const active = $derived(activeUpdateRequest(request));
  const hasContent = $derived(active || confirming !== null ||
    (status.configured && usableUpdate(status.available, now)) || ["failed", "cancelled"].includes(request?.state ?? "") || error !== null);

  async function submit(release: PublishedUpdate, mode: UpdateMode, confirmed = false): Promise<void> {
    if (submitting) return;
    if (!usableUpdate(release, Date.now())) {
      error = "This update expired. Refresh its availability.";
      return;
    }
    if (mode === "now" && needsActivityConfirmation(status.activity) && !confirmed) {
      confirming = release;
      return;
    }
    submitting = true;
    error = null;
    try {
      await requestUpdate(release, mode, confirmed);
      confirming = null;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "Update request failed";
    } finally {
      await queryClient.invalidateQueries({ queryKey: ["updates"] });
      submitting = false;
    }
  }

  async function cancel(): Promise<void> {
    if (!request || !canCancelUpdate(request) || submitting) return;
    submitting = true;
    error = null;
    try { await cancelUpdate(request.request_id); }
    catch (cause) { error = cause instanceof Error ? cause.message : "Cancellation failed"; }
    finally {
      await queryClient.invalidateQueries({ queryKey: ["updates"] });
      submitting = false;
    }
  }
</script>

{#if hasContent}
<div class="update-actions" data-update-activity={status.activity}>
  {#if !active && request?.state === "cancelled"}<span>Update canceled before installation. The previous version stayed active.</span>{/if}
  {#if !active && request?.state === "failed"}<span role="alert">Update failed</span>{/if}
  {#if active && request}
    <span>{request.state === "applying" ? "Update in progress" : request.mode === "idle" && request.state === "pending" ? "Update waits for idle" : "Update requested"}</span>
    {#if canCancelUpdate(request)}<button type="button" class="ghost" disabled={submitting} onclick={cancel}>Cancel update</button>{/if}
  {:else if confirming}
    <span>{status.activity === "busy" ? "Work is active. The update can interrupt it." : "Activity is unknown. The update can interrupt work."}</span>
    {#if usableUpdate(confirming, now)}
      <button type="button" class="ghost" disabled={submitting} onclick={() => submit(confirming!, "now", true)}>Proceed with update</button>
    {:else}<span>This update expired. Refresh its availability.</span>{/if}
    <button type="button" class="ghost" disabled={submitting} onclick={() => { confirming = null; }}>Go back</button>
  {:else if status.configured && usableUpdate(status.available, now)}
    <!-- Availability is independent of request state. Only pending, dispatched, and
         applying block a new request. After a failed request, the change-stream
         refetch restores both actions while the release remains usable. If the
         release expired or disappeared, the failure has no retry actions. -->
    <button type="button" class="primary" disabled={submitting} onclick={() => submit(status.available!, "idle")}>Update when idle</button>
    <button type="button" class="ghost" disabled={submitting} onclick={() => submit(status.available!, "now")}>Start update</button>
  {/if}
  {#if error}<span role="alert">{error}</span>{/if}
</div>
{/if}

<style>
  .update-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--space-2);
    width: 100%;
    /* The old status-card set this slot to --type-xs (11px). Set it here explicitly:
       the update controls no longer rely on a card or ancestor for their type scale. */
    font-size: var(--type-xs);
  }
  .update-actions > span { flex-basis: 100%; color: var(--text-faint); }
  .update-actions > span[role="alert"] { color: var(--accent-error); }

  /* Replace the old generic padding override, which fought the button classes.
     These are the app's primary/ghost pills, scaled to this --type-xs slot. Keep
     padding on each identity so a generic ancestor cannot flatten their spacing. */
  .update-actions button { font: inherit; cursor: pointer; border: 0; border-radius: var(--radius-pill); font-weight: 500; }
  .update-actions button:disabled { opacity: .5; cursor: default; }
  /* The card-free slot clips overflow for shell transitions. Keep keyboard focus
     inside the pills so its outline remains visible without card padding. */
  .update-actions button:focus-visible { outline-offset: calc(-1 * var(--border-strong)); }
  .update-actions .primary { background: var(--accent-bright); color: var(--surface-ink); padding: 4px 12px; }
  .update-actions .primary:focus-visible { outline-color: var(--surface-ink); }
  .update-actions .ghost { background: transparent; color: var(--text-faint); padding: 4px 10px; }
  .update-actions .ghost:hover:not(:disabled) { background-image: var(--interaction-hover); color: var(--text-strong); }
</style>
