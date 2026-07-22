<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import {
    mutateJsonWithResourceEffect,
    resourceCatalogue
  } from "../lib/resourceCatalogue";
  import type { WorkerTypeManifest } from "../lib/lifecycle";
  import { errorMessage, labelize } from "../lib/ui";
  import type {
    ManagedSkill,
    StageOwnershipMode,
    WorkerManagementDetail,
    WorkerManagementSettings,
    WorkerManagementSummary
  } from "../lib/types";

  let { workerType }: { workerType?: string } = $props();
  const stableWorkerType = untrack(() => workerType || null);

  const workers = stableWorkerType === null ? resourceCatalogue.workers() : null;
  const manifests = stableWorkerType === null ? resourceCatalogue.workerTypeManifests() : null;
  const worker = stableWorkerType ? resourceCatalogue.worker(stableWorkerType) : null;

  const ownerOptions: Array<{ value: StageOwnershipMode; label: string }> = [
    { value: "worker", label: "worker" },
    { value: "user", label: "user" },
    { value: "paired", label: "paired" }
  ];

  let selectedOwners = $state<Record<string, StageOwnershipMode>>({});
  let lastServerOwners = $state<Record<string, StageOwnershipMode>>({});
  let stageSaving = $state<Record<string, boolean>>({});
  let stageSaveErrors = $state<Record<string, unknown>>({});

  let indexedManifests = $derived(new Map((manifests?.data?.worker_types || []).map((item) => [item.worker_type, item])));

  function stageCount(workerSummary: WorkerManagementSummary): number {
    return indexedManifests.get(workerSummary.worker_type)?.stages.length || 0;
  }

  function detailManifest(detail: WorkerManagementDetail | undefined): WorkerTypeManifest | undefined {
    return detail?.manifest;
  }

  function displaySkillForEdit(settings: WorkerManagementSettings): ManagedSkill {
    return settings.candidate_specialist_skill || settings.specialist_skill;
  }

  async function saveSkillField(
    settings: WorkerManagementSettings,
    field: "description" | "markdown_body",
    raw: string
  ): Promise<void> {
    await mutateJsonWithResourceEffect<WorkerManagementSettings>(
      `/api/workers/${encodeURIComponent(settings.worker_type)}/skill`,
      {
        method: "PATCH",
        body: { [field]: raw }
      },
      { kind: "workerSettingsChanged", workerType: settings.worker_type }
    );
  }

  async function saveStageOwner(settings: WorkerManagementSettings, stage: string, ownershipMode: StageOwnershipMode): Promise<void> {
    selectedOwners = { ...selectedOwners, [stage]: ownershipMode };
    stageSaving = { ...stageSaving, [stage]: true };
    stageSaveErrors = { ...stageSaveErrors, [stage]: null };
    try {
      await mutateJsonWithResourceEffect<WorkerManagementSettings>(
        `/api/workers/${encodeURIComponent(settings.worker_type)}/stages/${encodeURIComponent(stage)}/default-ownership`,
        { method: "PUT", body: { ownership_mode: ownershipMode } },
        { kind: "workerSettingsChanged", workerType: settings.worker_type }
      );
    } catch (err) {
      stageSaveErrors = { ...stageSaveErrors, [stage]: err };
    } finally {
      stageSaving = { ...stageSaving, [stage]: false };
    }
  }

  function stageOwnerValue(settings: WorkerManagementSettings, stage: string): StageOwnershipMode {
    return selectedOwners[stage] ?? settings.stage_ownership_defaults[stage] ?? "worker";
  }

  function sameOwners(
    left: Record<string, StageOwnershipMode>,
    right: Record<string, StageOwnershipMode>
  ): boolean {
    const leftEntries = Object.entries(left);
    if (leftEntries.length !== Object.keys(right).length) return false;
    return leftEntries.every(([stage, mode]) => right[stage] === mode);
  }

  function gatedFieldLabel(manifest: WorkerTypeManifest, fieldId: string | null): string {
    if (!fieldId) return "";
    return manifest.fields.find((field) => field.id === fieldId)?.label || labelize(fieldId);
  }

  $effect(() => {
    const settings = worker?.data?.settings;
    if (!settings) return;
    const next = { ...selectedOwners };
    const nextServer = { ...lastServerOwners };
    let selectedChanged = false;
    for (const [stage, mode] of Object.entries(settings.stage_ownership_defaults)) {
      const previousServerMode = lastServerOwners[stage];
      nextServer[stage] = mode;
      if (stageSaving[stage] || stageSaveErrors[stage]) continue;
      if (next[stage] === undefined || next[stage] === previousServerMode) {
        next[stage] = mode;
        selectedChanged = true;
      }
    }
    if (selectedChanged && !sameOwners(selectedOwners, next)) selectedOwners = next;
    if (!sameOwners(lastServerOwners, nextServer)) lastServerOwners = nextServer;
  });

  onDestroy(() => {
    workers?.dispose();
    manifests?.dispose();
    worker?.dispose();
  });
</script>

<section
  class="workers-screen"
  data-screen="workers"
  data-worker-id={stableWorkerType || undefined}
>
  {#if stableWorkerType === null}
    <div class="workers-page workers-page--index">
      <header class="workers-head">
        <h1>Workers</h1>
      </header>
      <ResourceState error={workers?.error || manifests?.error} loading={workers?.loading || manifests?.loading} hasData={Boolean(workers?.data && manifests?.data)} loadingText="Loading workers...">
        {#if workers?.data}
          <div class="workers-list" data-workers-list>
            {#each workers.data.workers as item}
              <a
                class="workers-row"
                href={`#/workers/${encodeURIComponent(item.worker_type)}`}
                data-worker-row
                data-worker-id={item.worker_type}
              >
                <span class="workers-row-main">
                  <span class="workers-row-title" data-worker-label>{item.label}</span>
                  <span class="workers-row-id" data-worker-structural-id>{item.worker_type}</span>
                </span>
                <span class="workers-row-meta" data-worker-skill-name>{item.specialist_skill_name}</span>
                <span class="workers-row-count" data-worker-stage-count>{stageCount(item)} Stages</span>
              </a>
            {/each}
          </div>
        {/if}
      </ResourceState>
    </div>
  {:else}
    <div class="workers-page workers-page--detail">
      <a class="workers-back" href="#/workers" data-workers-back>Back to Workers</a>
      <ResourceState error={worker?.error} loading={worker?.loading} hasData={Boolean(worker?.data)} loadingText="Loading worker...">
        {#if worker?.data}
          {@const detail = worker.data}
          {@const manifest = detailManifest(detail)}
          {@const editableSkill = displaySkillForEdit(detail.settings)}
          <article class="worker-detail" data-worker-detail data-worker-id={detail.settings.worker_type}>
            <header class="workers-head worker-detail-head">
              <h1 data-worker-name>{manifest?.label || labelize(detail.settings.worker_type)}</h1>
              <div class="worker-detail-facts">
                <span data-worker-structural-id>{detail.settings.worker_type}</span>
                <span data-worker-skill-name>{detail.settings.specialist_skill.name}</span>
                <span data-worker-stage-count>{manifest?.stages.length || 0} Stages</span>
              </div>
            </header>

            {#if manifest}
              <div class="worker-stage-table-shell">
                <table class="worker-stage-table" data-worker-stage-table>
                  <thead>
                    <tr>
                      <th>Stage</th>
                      <th class="worker-stage-gated-field">Gated field</th>
                      <th>Default owner</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each manifest.stages as stage}
                      <tr data-worker-stage-row data-stage={stage.id} data-terminal={stage.is_terminal ? "true" : "false"}>
                        <td data-stage-label>
                          <span class="worker-stage-name">{stage.label}</span>
                          <span class="worker-stage-id">{stage.id}</span>
                        </td>
                        <td class="worker-stage-gated-field" data-gated-field>{gatedFieldLabel(manifest, stage.gating_field)}</td>
                        <td data-default-owner={stage.is_terminal ? "" : stageOwnerValue(detail.settings, stage.id)}>
                          {#if stage.is_terminal}
                            <span class="worker-readonly-owner" data-terminal-owner>terminal</span>
                          {:else}
                            <select
                              class="worker-owner-select"
                              aria-label={`Default owner for ${stage.label}`}
                              data-stage-owner-select
                              data-saving={stageSaving[stage.id] ? "true" : "false"}
                              value={stageOwnerValue(detail.settings, stage.id)}
                              onchange={(event) => void saveStageOwner(detail.settings, stage.id, event.currentTarget.value as StageOwnershipMode)}
                            >
                              {#each ownerOptions as option}
                                <option value={option.value}>{option.label}</option>
                              {/each}
                            </select>
                            {#if stageSaveErrors[stage.id]}
                              <div class="worker-row-error" data-stage-owner-error>{errorMessage(stageSaveErrors[stage.id])}</div>
                            {/if}
                          {/if}
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}

            <section class="worker-skill" data-worker-skill>
              <header class="worker-skill-head">
                <h2>Specialist skill</h2>
              </header>

              <div class="worker-skill-content" data-skill-content>
                <dl class="worker-skill-meta">
                  <div>
                    <dt>Name</dt>
                    <dd data-skill-name>{detail.settings.specialist_skill.name}</dd>
                  </div>
                  <div>
                    <dt>Description</dt>
                    <dd data-skill-description>
                      <InlineEdit
                        value={editableSkill.description}
                        multiline
                        ariaLabel="Specialist skill description"
                        className="worker-skill-inline"
                        onSave={(raw) => saveSkillField(detail.settings, "description", raw)}
                      />
                    </dd>
                  </div>
                </dl>
                <div class="worker-skill-field">
                  <span>Markdown body</span>
                  <div class="worker-skill-body" data-skill-body>
                    <InlineEdit
                      value={editableSkill.markdown_body}
                      markdown
                      multiline
                      placeholder="Write the complete specialist skill body..."
                      ariaLabel="Specialist skill Markdown body"
                      className="worker-skill-body-editable"
                      onSave={(raw) => saveSkillField(detail.settings, "markdown_body", raw)}
                    />
                  </div>
                </div>
              </div>
            </section>
          </article>
        {/if}
      </ResourceState>
    </div>
  {/if}
</section>
