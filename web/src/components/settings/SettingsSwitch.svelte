<script lang="ts">
  /* The one Settings switch. It has the Focus switch's geometry. On is the neutral primary
     fill, not the accent: the accent means "needs you". */
  let { checked, label, disabled = false, onchange }: {
    checked: boolean;
    label: string;
    disabled?: boolean;
    onchange: (next: boolean) => void;
  } = $props();
</script>

<button type="button" class="settings-switch" role="switch" aria-checked={checked} aria-label={label} {disabled} onclick={() => onchange(!checked)}></button>

<style>
  .settings-switch { position:relative; flex:none; width:34px; height:20px; padding:0; border:0; border-radius:var(--radius-pill); background:var(--surface-input); box-shadow:inset 0 0 0 1px rgb(255 255 255 / 8%); cursor:pointer; transition:background var(--motion-fast) var(--motion-ease); }
  .settings-switch::after { content:""; position:absolute; top:3px; left:3px; width:14px; height:14px; border-radius:50%; background:var(--tone-secondary); transition:transform var(--motion-base) var(--motion-ease), background var(--motion-fast) var(--motion-ease); }
  .settings-switch:hover::after { background:var(--tone-strong); }
  .settings-switch[aria-checked="true"] { background:var(--button-primary-fill); box-shadow:none; }
  .settings-switch[aria-checked="true"]::after { transform:translateX(14px); background:var(--button-primary-ink); }
  .settings-switch:disabled { cursor:default; opacity:.5; }
  .settings-switch:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--focus-offset); }
  @media (prefers-reduced-motion: reduce) { .settings-switch, .settings-switch::after { transition:none; } }
  @media (forced-colors: active) { .settings-switch { border:1px solid ButtonText; } .settings-switch[aria-checked="true"]::after { background:Highlight; } }
</style>
