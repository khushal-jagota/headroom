<script lang="ts">
  import { performUpdateAction, type UpdateActionRequest } from "../lib/updates/api";
  import type { UpdateStatus } from "../lib/updates/contracts";
  import { updateDetails, type UpdateFailureState } from "../lib/updates/logic/presentation";

  type DialogKind = "offer" | "waiting" | UpdateFailureState;
  let { status, now }: { status: UpdateStatus; now: number } = $props();
  let dialog = $state<HTMLDialogElement | null>(null);
  let kind = $state<DialogKind>("offer");
  let error = $state<string | null>(null);
  const id = $props.id();
  const details = $derived(kind === "offer" || kind === "waiting" ? null : updateDetails(kind, status, now));
  const allows = (action: UpdateStatus["allowed_actions"][number]) => status.allowed_actions.includes(action);
  const canRetry = $derived(kind === "failed" ? allows("retry") : allows("retry_mac"));

  export function openDialog(view: DialogKind, message: string | null = null): void {
    kind = view;
    error = message;
    if (!dialog?.open) dialog?.showModal();
  }

  /** Close at once. The card shows the press, and a refusal reopens this dialog with it. */
  export async function perform(request: UpdateActionRequest, from: DialogKind = kind): Promise<void> {
    dialog?.close();
    try {
      await performUpdateAction(status, request);
    } catch (cause) {
      openDialog(from, cause instanceof Error ? cause.message : "The update action failed.");
    }
  }

  // A server transition can make an open decision obsolete without a local click.
  $effect(() => {
    if (dialog?.open && status.state !== (kind === "offer" ? "available" : kind)) dialog.close();
  });
</script>

<dialog bind:this={dialog} aria-labelledby={id} data-update-dialog={kind} data-update-activity={status.activity}>
  <div class="dlg">
    {#if kind === "offer"}
      <h2 id={id}>Update Panels</h2>
      <p>A new version is ready for Panels and the Mac. Panels restarts, then you refresh to finish.</p>
      {#if status.activity !== "idle"}
        <div class="note"><span class="status-mark" aria-hidden="true"></span><span>{status.activity === "busy" ? "Work is running. Updating now interrupts it." : "Panels cannot tell if work is running. Updating now can interrupt it."}</span></div>
      {/if}
    {:else if kind === "waiting"}
      <h2 id={id}>Update waits for work to finish</h2>
      <p>Panels updates as soon as running work finishes.</p>
    {:else if details}
      <h2 id={id}>{details.title}</h2>
      <dl class="facts">
        {#each details.facts as [label, value] (label)}<dt>{label}</dt><dd>{value}</dd>{/each}
      </dl>
    {/if}
    {#if error}<p role="alert">{error}</p>{/if}
    <footer>
      {#if kind === "offer"}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Not now</button>
        {#if allows("update") && status.available}
          {#if status.activity !== "idle"}<button type="button" class="pill pill--ghost" onclick={() => perform({ action: "install", mode: "idle" })}>When work finishes</button>{/if}
          <button type="button" class="pill pill--primary" onclick={() => perform({ action: "install", mode: "now" })}>Update now</button>
        {/if}
      {:else if kind === "waiting"}
        {#if allows("cancel")}<button type="button" class="pill pill--ghost" onclick={() => perform({ action: "cancel" })}>Cancel update</button>{/if}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Close</button>
        {#if allows("update_now")}<button type="button" class="pill pill--primary" onclick={() => perform({ action: "now" })}>Update now</button>{/if}
      {:else}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Close</button>
        {#if canRetry && status.attempt}<button type="button" class="pill pill--primary" onclick={() => perform({ action: kind === "failed" ? "retry" : "retry_mac" })}>Try again</button>{/if}
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
  .facts { display: grid; grid-template-columns: auto 1fr; gap: var(--space-1) var(--space-4); margin: 0; color: var(--text-muted); overflow-wrap: anywhere; }
  .facts dt { color: var(--text-faint); }
  .facts dd { margin: 0; color: var(--text-default); }
  footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); }
  .pill { cursor: pointer; font: inherit; border: 0; border-radius: var(--radius-pill); padding: 6px 14px; font-weight: 530; transition: background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .pill--primary { background: var(--accent-bright); color: var(--surface-ink); }
  .pill--ghost { background: transparent; color: var(--text-faint); }
  .pill:hover { background-image: var(--interaction-hover); }
  .pill--ghost:hover { color: var(--text-strong); }
  .pill:focus-visible { outline: var(--border-strong) solid var(--focus-ring); outline-offset: 2px; }
  @keyframes dialog-in { from { opacity: 0; transform: translateY(var(--space-1)); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { dialog[open] { animation: none; } .pill { transition: none; } }
</style>
