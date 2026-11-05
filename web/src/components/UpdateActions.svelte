<script lang="ts">
  import { queryClient } from "../lib/queryClient";
  import { cancelUpdate, promoteUpdate, requestUpdate } from "../lib/updates/api";
  import type { UpdateMode, UpdateStatus } from "../lib/updates/contracts";
  import { usableUpdate } from "../lib/updates/logic/actions";

  let { status, now }: { status: UpdateStatus; now: number } = $props();
  let dialog = $state<HTMLDialogElement | null>(null);
  let kind = $state<"offer" | "waiting" | "failed">("offer");
  let submitting = $state(false);
  let error = $state<string | null>(null);
  const id = $props.id();
  const canOffer = $derived(status.allowed_actions.includes("update") && usableUpdate(status.available, now));
  const canRetry = $derived(status.allowed_actions.includes("retry") && usableUpdate(status.available, now));

  export function openDialog(view: "offer" | "waiting" | "failed"): void {
    kind = view;
    error = null;
    dialog?.showModal();
  }

  async function perform(action: "cancel" | UpdateMode): Promise<void> {
    if (submitting) return;
    submitting = true;
    error = null;
    try {
      if (action === "cancel" && status.request) {
        await cancelUpdate(status.request.request_id);
      } else if (kind === "waiting" && action === "now" && status.request) {
        await promoteUpdate(status.request.request_id);
      } else if (action !== "cancel" && usableUpdate(status.available, Date.now())) {
        await requestUpdate(status.available, action, action === "now");
      } else {
        throw new Error("This update expired. Close this dialog to check availability.");
      }
      dialog?.close();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "Update request failed";
    } finally {
      await queryClient.invalidateQueries({ queryKey: ["updates"] });
      submitting = false;
    }
  }

  // A server transition can make an open decision obsolete without a local click.
  $effect(() => {
    if (dialog?.open && !submitting && !["available", "waiting", "failed"].includes(status.state)) dialog.close();
  });
</script>

<dialog bind:this={dialog} aria-labelledby={id} data-update-dialog data-update-activity={status.activity}>
  <div class="dlg">
    <h2 id={id}>{kind === "offer" ? "Update Panels" : kind === "waiting" ? "Update waits for work to finish" : "Update didn't install"}</h2>
    <p>{kind === "offer" ? "A new version is ready. Panels restarts, then you refresh to finish." : kind === "waiting" ? "Panels updates as soon as running work finishes." : (status.detail ?? "Panels could not finish this update.")}</p>
    {#if kind === "offer" && status.activity !== "idle"}
      <div class="note"><span class="status-mark" aria-hidden="true"></span><span>{status.activity === "busy" ? "Work is running. Updating now interrupts it." : "Panels cannot tell if work is running. Updating now can interrupt it."}</span></div>
    {/if}
    {#if error}<p role="alert">{error}</p>{/if}
    <footer>
      {#if kind === "waiting"}
        {#if status.allowed_actions.includes("cancel")}<button type="button" class="pill pill--ghost" disabled={submitting} onclick={() => perform("cancel")}>Cancel update</button>{/if}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Close</button>
        {#if status.allowed_actions.includes("update_now")}<button type="button" class="pill pill--primary" disabled={submitting} onclick={() => perform("now")}>Update now</button>{/if}
      {:else}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Not now</button>
        {#if kind === "offer" && canOffer}
          {#if status.activity !== "idle"}<button type="button" class="pill pill--ghost" disabled={submitting} onclick={() => perform("idle")}>When work finishes</button>{/if}
          <button type="button" class="pill pill--primary" disabled={submitting} onclick={() => perform("now")}>Update now</button>
        {:else if kind === "failed" && canRetry}
          <button type="button" class="pill pill--primary" disabled={submitting} onclick={() => perform("now")}>Try again</button>
        {/if}
      {/if}
    </footer>
  </div>
</dialog>

<style>
  dialog { width: min(460px, calc(100% - var(--space-5))); margin: auto; padding: 0; border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-lg); background: var(--surface-2); color: var(--text-default); box-shadow: var(--shadow-float); }
  dialog[open] { animation: dialog-in var(--motion-slow) var(--motion-ease); }
  dialog::backdrop { background: var(--scrim); }
  .dlg { padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }
  h2 { font-size: var(--type-md); font-weight: 600; color: var(--text-strong); margin: 0; }
  p { color: var(--text-muted); line-height: 1.45; margin: 0; }
  p[role="alert"] { color: var(--accent-error); }
  .note { display: flex; gap: var(--space-2); align-items: baseline; color: var(--accent-attention); }
  .status-mark { width: 7px; height: 7px; flex: none; border-radius: var(--radius-pill); background: var(--accent-attention); transform: translateY(-1px); }
  footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); }
  .pill { cursor: pointer; font: inherit; border: 0; border-radius: var(--radius-pill); padding: 6px 14px; font-weight: 500; transition: background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .pill--primary { background: var(--accent-bright); color: var(--surface-ink); }
  .pill--ghost { background: transparent; color: var(--text-faint); }
  .pill:hover { background-image: var(--interaction-hover); }
  .pill--ghost:hover { color: var(--text-strong); }
  .pill:focus-visible { outline: var(--border-strong) solid var(--accent-bright); outline-offset: 2px; }
  .pill:disabled { opacity: .5; cursor: default; }
  @keyframes dialog-in { from { opacity: 0; transform: translateY(var(--space-1)); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { dialog[open] { animation: none; } .pill { transition: none; } }
</style>
