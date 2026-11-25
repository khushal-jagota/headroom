<script lang="ts">
  import { tick } from "svelte";
  import { updatePause } from "../lib/updates/writePause";
  import { recoveredDrafts, dismissRecoveredDraft } from "../lib/updates/handoff";
  let panel = $state<HTMLElement | null>(null);
  let dialog = $state<HTMLDialogElement | null>(null);
  let previousFocus: HTMLElement | null = null;
  $effect(() => {
    if ($updatePause.blocked && dialog && !dialog.open) {
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal();
      void tick().then(() => panel?.focus({ preventScroll: true }));
    } else if (!$updatePause.blocked) {
      dialog?.close();
      previousFocus?.focus({ preventScroll: true });
      previousFocus = null;
    }
  });
  function holdFocus(event: KeyboardEvent): void {
    if (event.key === "Tab" || event.key === "Escape") event.preventDefault();
    event.stopPropagation();
  }
</script>

<dialog class="update-pause" bind:this={dialog} data-update-pause aria-labelledby="update-pause-title" aria-describedby="update-pause-detail" oncancel={(event) => event.preventDefault()} onkeydown={holdFocus}>
    <section class="update-pause-panel" bind:this={panel} tabindex="-1">
      <div class="update-pause-spinner" aria-hidden="true"></div>
      <div role="status" aria-live="polite">
        <h2 id="update-pause-title">{$updatePause.openingScreens ? "Opening the updated Panels" : "Panels is updating"}</h2>
        <p id="update-pause-detail">Please wait. Your work stays here.</p>
      </div>
    </section>
  </dialog>
{#if !$updatePause.blocked && $recoveredDrafts.length}
  <aside class="recovered-drafts" aria-label="Work retained through the update" data-update-recovered-drafts>
    <h2>Your work stayed here</h2>
    <p>Review these entries before you continue.</p>
    {#each $recoveredDrafts as draft (draft.id)}
      <section>
        <label for={`recovered-${draft.id}`}>{draft.label}</label>
        <textarea id={`recovered-${draft.id}`} readonly value={draft.value}></textarea>
        <button type="button" onclick={() => dismissRecoveredDraft(draft.id)}>Done with this entry</button>
      </section>
    {/each}
  </aside>
{/if}

<style>
  .update-pause { position: fixed; inset: 0; max-width: none; max-height: none; width: 100%; height: 100%; margin: 0; padding: 0; border: 0; background: color-mix(in srgb, var(--surface-1) 94%, transparent); color: var(--text-default); }
  .update-pause[open] { display: grid; place-items: center; }
  .update-pause::backdrop { background: var(--scrim); }
  .update-pause-panel { max-width: var(--setup-column-width); padding: var(--space-5); text-align: center; outline: none; }
  .update-pause-panel h2 { margin: var(--space-4) 0 var(--space-2); color: var(--text-strong); font-size: var(--type-page-title); font-weight: var(--weight-title); }
  .update-pause-panel p { margin: 0; color: var(--text-muted); font-size: var(--type-body); }
  .update-pause-spinner { margin: auto; width: var(--space-5); height: var(--space-5); border: var(--border-hairline) solid var(--border-color); border-top-color: var(--accent-bright); border-radius: var(--radius-pill); animation: update-spin var(--motion-loop-spin) linear infinite; }
  .recovered-drafts { position: fixed; right: var(--space-4); bottom: var(--space-4); z-index: 10000; width: min(var(--setup-column-width), calc(100% - var(--space-5))); max-height: 80dvh; overflow: auto; padding: var(--space-4); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-lg); background: var(--surface-2); box-shadow: var(--shadow-float); }
  .recovered-drafts h2 { font-size: var(--type-row); margin: 0; }
  .recovered-drafts p { color: var(--text-muted); }
  .recovered-drafts section { display: flex; flex-direction: column; gap: var(--space-2); margin-top: var(--space-3); }
  .recovered-drafts textarea { min-height: var(--setup-top-max); }
  .recovered-drafts button { align-self: flex-end; }
  @keyframes update-spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .update-pause-spinner { animation: none; } }
</style>
