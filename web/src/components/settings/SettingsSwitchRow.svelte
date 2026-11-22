<script lang="ts">
  import { untrack } from "svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSwitch from "./SettingsSwitch.svelte";
  import { createOptimisticSwitch, toggleOptimistically } from "../../lib/settings/optimisticSwitch";

  /* A row whose control is a switch that saves at once. The switch moves when pressed; if
     the save fails it moves back and the error shows under the row. */
  let { label, description, rowId, checked, save, switchLabel = label, failureText = "Could not save. Panels did not change this setting." }: {
    label: string;
    description?: string;
    rowId?: string;
    /** The saved value. The switch follows it whenever no save is in flight. */
    checked: boolean;
    save: (next: boolean) => Promise<unknown>;
    switchLabel?: string;
    failureText?: string;
  } = $props();

  const switchState = $state(createOptimisticSwitch(untrack(() => checked)));
  let pending = 0;
  // A new saved value moves the switch, unless a press is still saving. Ending a save does
  // not, so the switch does not flick back while the page's read catches up.
  $effect.pre(() => { const saved = checked; if (pending === 0) switchState.shown = saved; });

  async function toggle(next: boolean): Promise<void> {
    pending += 1;
    try { await toggleOptimistically(switchState, next, save, failureText); }
    finally { pending -= 1; }
  }
</script>

<SettingsRow {label} {description} {rowId} error={switchState.error}>
  {#snippet control()}<SettingsSwitch checked={switchState.shown} label={switchLabel} onchange={next => void toggle(next)} />{/snippet}
</SettingsRow>
