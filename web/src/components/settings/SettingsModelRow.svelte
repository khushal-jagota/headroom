<script lang="ts">
  import { createEmployeeConfigurationEditor } from "../../lib/conversation/employeeConfigurationEditor.svelte";
  import type { EmployeeConfigurationSnapshot } from "../../lib/types";
  import { errorMessage } from "../../lib/ui";
  import UnifiedModelPicker from "../conversation/UnifiedModelPicker.svelte";
  import SkeletonRegion from "../SkeletonRegion.svelte";
  import SettingsRow from "./SettingsRow.svelte";

  /* The "Model" row of an agent or Worker type: the product's model and effort picker,
     which saves the launch defaults as soon as a model is chosen. A failed save puts the
     picker back and says so under the row. */
  let { description, rowId, value, onSave }: {
    description?: string;
    rowId: string;
    value: EmployeeConfigurationSnapshot;
    onSave: (next: EmployeeConfigurationSnapshot) => Promise<EmployeeConfigurationSnapshot>;
  } = $props();

  const editor = createEmployeeConfigurationEditor(() => value, (next) => onSave(next));
  const failure = "Could not save. Panels did not change the model.";
  let error = $derived.by(() => {
    if (editor.saveError) {
      const detail = errorMessage(editor.saveError);
      return detail === "request failed" ? failure : `${failure} ${detail}`;
    }
    return editor.backendsError ? errorMessage(editor.backendsError) : null;
  });
</script>

<SettingsRow label="Model" {description} {rowId} {error}>
  {#snippet control()}
    <div class="settings-model-control" data-launch-defaults>
      <SkeletonRegion shape="inline" loading={editor.loading} loadingText="Loading models…" />
      <UnifiedModelPicker
        view={editor.picker}
        bind:snapshots={editor.backends}
        models={editor.snapshot?.available_models ?? []}
        backendEffortOptions={editor.snapshot?.reasoning_effort_options ?? []}
        below
        disabled={editor.saving}
        keepOpenWhenDisabled
        attributes={{ "data-launch-defaults-picker": "" }}
        onChooseBackend={editor.chooseBackend}
        onChooseModel={editor.chooseModel}
      />
    </div>
  {/snippet}
</SettingsRow>

<style>
  .settings-model-control { display:flex; align-items:center; gap:var(--space-2); flex:none; min-width:0; }
</style>
