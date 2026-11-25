<script lang="ts">
  /* A signed-out backend that Panels cannot sign in itself (Claude). The owner runs its login
   * command in their own terminal on that machine. Panels never copies a login between
   * machines and never runs the terminal. Check again reads the status once more. */
  import Button from "../Button.svelte";

  let { label, machineName, command, checking, onCheck }: {
    label: string;
    machineName: string;
    command: string;
    checking: boolean;
    onCheck: () => void;
  } = $props();

  let copied = $state(false);
  async function copy() {
    await navigator.clipboard.writeText(command);
    copied = true;
    setTimeout(() => { copied = false; }, 2000);
  }
</script>

<div class="guided" data-backend-guided-sign-in>
  <p class="guided-step">
    1. On <b>{machineName}</b>, open a terminal and run this command. If that machine has no screen, connect to it over SSH first.
  </p>
  <pre class="guided-code" data-backend-guided-command>{command}<Button onclick={() => void copy()} aria-label="Copy command">{copied ? "Copied" : "Copy"}</Button></pre>
  <p class="guided-step">
    2. The command prints a link. Open the link in a browser on any device, sign in, and give the code it shows back to the terminal.
  </p>
  <p class="guided-step">
    3. Come back here and check again.
  </p>
  <div class="guided-actions">
    <Button variant="primary" disabled={checking} onclick={onCheck} data-backend-guided-check>{checking ? "Checking…" : "Check again"}</Button>
  </div>
  <p class="guided-note">
    {label} stays signed in on {machineName} only. Panels does not copy a login to another machine.
  </p>
</div>

<style>
  .guided { display:grid; gap:var(--space-2); padding:var(--space-3) 0 var(--space-1); max-width:64ch; }
  .guided-step { margin:0; font-size:var(--type-sm); color:var(--text-default); }
  .guided-code { position:relative; margin:0; padding:var(--space-3); padding-right:calc(var(--space-7) + var(--space-6)); background:var(--surface-recessed); border-radius:var(--radius-md); font:var(--type-code)/1.6 var(--font-mono); color:var(--tone-primary); white-space:pre-wrap; overflow-wrap:anywhere; }
  .guided-code :global(.button) { position:absolute; top:var(--space-2); right:var(--space-2); font-family:var(--font-ui); }
  .guided-actions { display:flex; gap:var(--space-2); }
  .guided-note { margin:0; font-size:var(--type-xs); color:var(--text-faint); }
</style>
