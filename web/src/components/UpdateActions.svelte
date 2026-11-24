<script lang="ts">
  import { performUpdateAction, type UpdateActionRequest } from "../lib/updates/api";
  import type { UpdateStatus } from "../lib/updates/contracts";
  import { deviceUpdateWarnings, interruptionText, type UpdateFailureState } from "../lib/updates/logic/presentation";
  import MachineRows from "../lib/updates/MachineRows.svelte";

  type DialogKind = "offer" | UpdateFailureState;
  let { status, now }: { status: UpdateStatus; now: number } = $props();
  let dialog = $state<HTMLDialogElement | null>(null);
  let kind = $state<DialogKind>("offer");
  let error = $state<string | null>(null);
  const id = $props.id();
  const impact = $derived(interruptionText(status.interruption));
  const allows = (action: UpdateStatus["allowed_actions"][number]) => status.allowed_actions.includes(action);
  const canRetry = $derived(kind === "failed" && allows("retry"));

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
      <p>{impact}</p>
      {#if status.interruption.stops_agents && status.interruption.agents.length}
        <p class="agent-names">{status.interruption.agents.join(", ")}</p>
      {/if}
      {#each deviceUpdateWarnings(status) as warning (warning)}<p class="device-warning">{warning}</p>{/each}
      <MachineRows machines={status.available_machines} />
    {:else}
      <h2 id={id}>Update did not install</h2>
      <p>{status.detail ?? status.attempt?.detail ?? "Panels kept its previous version."}</p>
    {/if}
    {#if error}<p role="alert">{error}</p>{/if}
    <footer>
      {#if kind === "offer"}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Not now</button>
        {#if allows("update") && status.available}
          <button type="button" class="pill pill--primary" onclick={() => perform({ action: "install" })}>Update</button>
        {/if}
      {:else}
        <button type="button" class="pill pill--ghost" onclick={() => dialog?.close()}>Close</button>
        {#if canRetry && status.attempt}<button type="button" class="pill pill--primary" onclick={() => perform({ action: "retry" })}>Try again</button>{/if}
      {/if}
    </footer>
  </div>
</dialog>

<style>
  dialog { width: min(460px, calc(100% - var(--space-5))); margin: auto; padding: 0; border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-lg); background: var(--surface-2); color: var(--text-default); box-shadow: var(--shadow-float); }
  dialog[open] { animation: dialog-in var(--motion-slow) var(--motion-ease); }
  dialog::backdrop { background: var(--scrim); }
  .dlg { padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }
  h2 { font-size: var(--type-page-section); font-weight: var(--weight-title); color: var(--text-strong); margin: 0; }
  p { color: var(--text-muted); line-height: 1.45; margin: 0; }
  p[role="alert"] { color: var(--accent-error); }
  .device-warning { color: var(--accent-attention); }
  .agent-names { color: var(--text-default); overflow-wrap: anywhere; }
  footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); }
  .pill { cursor: pointer; font: inherit; border: 0; border-radius: var(--radius-pill); padding: 6px 14px; font-weight: var(--weight-small); transition: background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .pill--primary { background: var(--accent-bright); color: var(--surface-ink); }
  .pill--ghost { background: transparent; color: var(--text-faint); }
  .pill:hover { background-image: var(--interaction-hover); }
  .pill--ghost:hover { color: var(--text-strong); }
  .pill:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: 2px; }
  @keyframes dialog-in { from { opacity: 0; transform: translateY(var(--space-1)); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { dialog[open] { animation: none; } .pill { transition: none; } }
</style>
