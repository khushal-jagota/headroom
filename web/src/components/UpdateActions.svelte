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
    (status.configured && usableUpdate(status.available, now)) || request?.state === "failed" || error !== null);

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
  {#if active && request}
    <span>{request.state === "applying" ? "Update in progress" : request.mode === "idle" && request.state === "pending" ? "Update waits for idle" : "Update queued"} · {request.release.manifest.app_sha.slice(0, 7)}</span>
    {#if request.detail}<span>{request.detail}</span>{/if}
    {#if canCancelUpdate(request)}<button type="button" class="button button--quiet" disabled={submitting} onclick={cancel}>Cancel update</button>{/if}
  {:else if confirming}
    <span>{status.activity === "busy" ? "Work is active. Update now can interrupt it." : "Activity is unknown. Update now can interrupt work."}</span>
    {#if usableUpdate(confirming, now)}
      <button type="button" class="button button--quiet" disabled={submitting} onclick={() => submit(confirming!, "now", true)}>Proceed with update</button>
    {:else}<span>This update expired. Refresh its availability.</span>{/if}
    <button type="button" class="button button--quiet" disabled={submitting} onclick={() => { confirming = null; }}>Go back</button>
  {:else if status.configured && usableUpdate(status.available, now)}
    <span>Update available · {status.available.manifest.app_sha.slice(0, 7)}</span>
    <button type="button" class="button button--quiet" disabled={submitting} onclick={() => submit(status.available!, "now")}>Update now</button>
    <button type="button" class="button button--quiet" disabled={submitting} onclick={() => submit(status.available!, "idle")}>Update when idle</button>
  {/if}
  {#if !active && request?.state === "failed"}<span>{request.detail ?? "Update failed"}</span>{/if}
  {#if error}<span role="alert">{error}</span>{/if}
</div>
{/if}

<style>
  .update-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); width: 100%; }
  .update-actions > span { flex-basis: 100%; }
  .update-actions button { font-size: inherit; padding: var(--space-1) var(--space-2); }
</style>
