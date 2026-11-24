<script lang="ts">
  import { tick } from "svelte";
  import { mutateJson } from "../../lib/mutate";
  import { SERVER_MOVE_DEFAULT_PORT } from "../../lib/machines/config";
  import type { Machine, ServerMoveRequest, ServerMoveView } from "../../lib/machines/contracts";
  import Button from "../Button.svelte";

  /* The "are you sure" step before the server moves to another machine. It stays open while
     the server answers, and a refusal shows the server's own sentence here. */
  let dialog = $state<HTMLDialogElement | null>(null);
  let addressField = $state<HTMLInputElement | null>(null);
  let target = $state<Machine | null>(null);
  let serverUrl = $state("");
  let port = $state<number | null>(SERVER_MOVE_DEFAULT_PORT);
  let sending = $state(false);
  let error = $state<string | null>(null);
  const id = $props.id();

  export async function openDialog(machine: Machine): Promise<void> {
    target = machine; serverUrl = ""; port = SERVER_MOVE_DEFAULT_PORT; error = null;
    if (!dialog?.open) dialog?.showModal();
    await tick();
    addressField?.focus();
  }

  async function move() {
    if (!target || port === null) return;
    sending = true; error = null;
    const body: ServerMoveRequest = { server_url: serverUrl.trim(), port };
    try {
      await mutateJson<ServerMoveView>(`/api/machines/${encodeURIComponent(target.machine_id)}/move`, { method: "POST", body });
      dialog?.close();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "The server did not start the move.";
    } finally { sending = false; }
  }
  function cancel(event: Event) { if (sending) event.preventDefault(); }
</script>

<dialog bind:this={dialog} aria-labelledby={id} oncancel={cancel} data-server-move-dialog>
  <form class="dlg" onsubmit={(event) => { event.preventDefault(); void move(); }}>
    <h2 id={id}>Move the server to {target?.name}?</h2>
    <p>The server pauses while it copies its state to {target?.name}. Work on the machines continues. Afterward, this server answers with the new address.</p>
    <label class="field">New server address
      <input class="input" type="url" bind:this={addressField} bind:value={serverUrl} required pattern="https://.+"
        placeholder="https://panels.example.com" autocomplete="off" disabled={sending} />
    </label>
    <label class="field">Port on {target?.name}
      <input class="input" type="number" bind:value={port} required min="1" max="65535" step="1" disabled={sending} />
    </label>
    {#if error}<p role="alert">{error}</p>{/if}
    <footer>
      <Button variant="ghost" disabled={sending} onclick={() => dialog?.close()}>Not now</Button>
      <Button type="submit" variant="primary" disabled={sending || !serverUrl.trim() || port === null}>{sending ? "Moving…" : "Move the server"}</Button>
    </footer>
  </form>
</dialog>

<style>
  dialog { width: min(460px, calc(100% - var(--space-5))); margin: auto; padding: 0; border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-lg); background: var(--surface-2); color: var(--text-default); box-shadow: var(--shadow-float); }
  dialog[open] { animation: dialog-in var(--motion-slow) var(--motion-ease); }
  dialog::backdrop { background: var(--scrim); }
  .dlg { padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }
  h2 { font-size: var(--type-page-section); font-weight: var(--weight-title); color: var(--text-strong); margin: 0; overflow-wrap: anywhere; }
  p { color: var(--text-muted); line-height: 1.45; margin: 0; overflow-wrap: anywhere; }
  p[role="alert"] { color: var(--accent-error); }
  .field { display: grid; gap: var(--space-1); font-size: var(--type-small); color: var(--tone-tertiary); }
  .input { width: 100%; min-height: 32px; padding: 0 var(--space-2); border: var(--border-hairline) solid var(--border-input); border-radius: var(--radius-md); background: var(--surface-input); color: var(--tone-primary); font: inherit; font-size: var(--type-row); }
  .input::placeholder { color: var(--tone-faint); font-style: italic; }
  footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); }
  @keyframes dialog-in { from { opacity: 0; transform: translateY(var(--space-1)); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { dialog[open] { animation: none; } }
</style>
