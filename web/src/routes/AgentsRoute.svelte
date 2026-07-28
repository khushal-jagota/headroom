<script lang="ts">
  import { untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import ManagedLaunchDefaults from "../components/ManagedLaunchDefaults.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import RoleSkillEditor from "../components/RoleSkillEditor.svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import type { WorkerTypeManifest } from "../lib/lifecycle";
  import { errorMessage, labelize } from "../lib/ui";
  import type {
    ChiefManagementSettings,
    EmployeeConfigurationSnapshot,
    ManagedSkill,
    StageOwnershipMode,
    WorkerManagementDetail,
    WorkerManagementSettings
  } from "../lib/types";

  type RoleKind = "index" | "agent" | "skill" | "worker";

  let { roleKind, roleId }: { roleKind: RoleKind; roleId?: string } = $props();
  const stableRoleKind = untrack(() => roleKind);
  const stableRoleId = untrack(() => roleId || null);

  // Each page of this screen reads only what it shows; the rest stay switched off.
  const workers = createQuery(() => ({
    ...queries.workers(),
    enabled: stableRoleKind === "index" || stableRoleKind === "agent"
  }));
  const skillsHome = createQuery(() => ({
    ...queries.skillsHome(),
    enabled: stableRoleKind === "index" || stableRoleKind === "skill"
  }));
  const worker = createQuery(() => ({
    ...queries.worker(stableRoleId ?? ""),
    enabled: stableRoleKind === "worker" && stableRoleId !== null
  }));

  const ownerOptions: Array<{ value: StageOwnershipMode; label: string }> = [
    { value: "worker", label: "worker" },
    { value: "user", label: "user" },
    { value: "paired", label: "paired" }
  ];

  let selectedOwners = $state<Record<string, StageOwnershipMode>>({});
  let lastServerOwners = $state<Record<string, StageOwnershipMode>>({});
  let stageSaving = $state<Record<string, boolean>>({});
  let stageSaveErrors = $state<Record<string, unknown>>({});

  let indexedSkills = $derived(
    new Map((skillsHome.data?.skills || []).map((skill) => [skill.name, skill]))
  );
  let sharedWorkerSkill = $derived(indexedSkills.get("panels-worker"));

  function displaySkillForEdit(settings: WorkerManagementSettings): ManagedSkill {
    return settings.candidate_specialist_skill || settings.specialist_skill;
  }

  async function saveWorkerSkillField(
    settings: WorkerManagementSettings,
    field: "description" | "markdown_body",
    raw: string
  ): Promise<void> {
    await mutateJson<WorkerManagementSettings>(
      `/api/workers/${encodeURIComponent(settings.worker_type)}/skill`,
      { method: "PATCH", body: { [field]: raw } }
    );
  }

  async function saveChiefSkillField(
    field: "description" | "markdown_body",
    raw: string
  ): Promise<void> {
    await mutateJson<ChiefManagementSettings>("/api/workers/chief-of-staff/skill", {
      method: "PATCH",
      body: { [field]: raw }
    });
  }

  async function saveSharedWorkerSkillField(
    field: "description" | "markdown_body",
    raw: string
  ): Promise<void> {
    await mutateJson<ManagedSkill>("/api/skills/panels-worker", {
      method: "PATCH",
      body: { [field]: raw }
    });
  }

  async function saveStageOwner(
    settings: WorkerManagementSettings,
    stage: string,
    ownershipMode: StageOwnershipMode
  ): Promise<void> {
    selectedOwners = { ...selectedOwners, [stage]: ownershipMode };
    stageSaving = { ...stageSaving, [stage]: true };
    stageSaveErrors = { ...stageSaveErrors, [stage]: null };
    try {
      await mutateJson<WorkerManagementSettings>(
        `/api/workers/${encodeURIComponent(settings.worker_type)}/stages/${encodeURIComponent(stage)}/default-ownership`,
        { method: "PUT", body: { ownership_mode: ownershipMode } }
      );
    } catch (err) {
      stageSaveErrors = { ...stageSaveErrors, [stage]: err };
    } finally {
      stageSaving = { ...stageSaving, [stage]: false };
    }
  }

  function saveWorkerLaunchDefaults(
    settings: WorkerManagementSettings,
    next: EmployeeConfigurationSnapshot
  ): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson<WorkerManagementSettings>(
      `/api/workers/${encodeURIComponent(settings.worker_type)}/launch-defaults`,
      { method: "PUT", body: next }
    ).then((saved) => saved.launch_defaults);
  }

  function saveChiefLaunchDefaults(
    next: EmployeeConfigurationSnapshot
  ): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson<ChiefManagementSettings>("/api/workers/chief-of-staff/launch-defaults", {
      method: "PUT",
      body: next
    }).then((saved) => saved.launch_defaults);
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
    const settings = worker.data?.settings;
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
</script>

<section
  class="agents-screen"
  data-screen="agents"
  data-role-kind={stableRoleKind}
  data-role-id={stableRoleId || undefined}
>
  {#if stableRoleKind === "index"}
    <div class="agents-page agents-page--index">
      <header class="agents-page-head">
        <h1>Agents</h1>
      </header>
      <ResourceState
        {...resourceStateForQueries(workers, skillsHome)}
        loadingText="Loading agents..."
      >
        {#if workers.data}
          <section class="agents-index-section" data-agents-section>
            <h2 class="agents-section-title">Agents</h2>
            <div class="agents-destination-list">
              <a
                class="agents-destination"
                href="#/agents/chief-of-staff"
                data-agent-destination
              >
                <span class="agents-destination-copy">
                  <span class="agents-destination-name" data-destination-name>
                    {workers.data.chief_of_staff.label}
                  </span>
                  <span class="agents-destination-description" data-destination-description>
                    {workers.data.chief_of_staff.skill.description}
                  </span>
                </span>
                <span class="agents-destination-arrow" aria-hidden="true">→</span>
              </a>
              {#if sharedWorkerSkill}
                <a
                  class="agents-destination"
                  href="#/agents/worker-skill"
                  data-agent-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>Worker skill</span>
                    <span class="agents-destination-description" data-destination-description>
                      {sharedWorkerSkill.description}
                    </span>
                  </span>
                  <span class="agents-destination-arrow" aria-hidden="true">→</span>
                </a>
              {/if}
            </div>
          </section>

          <section class="agents-index-section" data-workers-section>
            <h2 class="agents-section-title">Workers</h2>
            <div class="agents-destination-list" data-workers-list>
              {#each workers.data.workers as item}
                <a
                  class="agents-destination"
                  href={`#/agents/workers/${encodeURIComponent(item.worker_type)}`}
                  data-worker-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>{item.label}</span>
                    <span class="agents-destination-description" data-destination-description>
                      {indexedSkills.get(item.specialist_skill_name)?.description || ""}
                    </span>
                  </span>
                  <span class="agents-destination-arrow" aria-hidden="true">→</span>
                </a>
              {/each}
            </div>
          </section>
        {/if}
      </ResourceState>
    </div>
  {:else if stableRoleKind === "agent"}
    <div class="agents-page agents-page--detail">
      <a class="agents-back" href="#/agents" data-agents-back>← Back to Agents</a>
      <ResourceState
        error={workers.error}
        loading={workers.isFetching}
        hasData={Boolean(workers.data)}
        loadingText="Loading agent..."
      >
        {#if workers.data}
          {@const chief = workers.data.chief_of_staff}
          <article class="role-detail" data-agent-detail data-agent-id="chief_of_staff">
            <header class="agents-detail-head">
              <h1 data-role-name>{chief.label}</h1>
              <div class="role-detail-facts">
                <span data-role-structural-id>chief_of_staff</span>
                <span data-role-skill-name>{chief.skill.name}</span>
                <span>Agent · no ticket lifecycle</span>
              </div>
              <p data-role-purpose>{chief.skill.description}</p>
            </header>

            <ManagedLaunchDefaults
              label={chief.label}
              value={chief.launch_defaults}
              onSave={saveChiefLaunchDefaults}
            />

            <div data-agent-skill>
              <RoleSkillEditor
                heading="Skill"
                skill={chief.skill}
                descriptionAriaLabel="Chief skill description"
                bodyAriaLabel="Chief skill Markdown body"
                bodyPlaceholder="Write the complete Chief skill body..."
                onSave={saveChiefSkillField}
              />
            </div>
          </article>
        {/if}
      </ResourceState>
    </div>
  {:else if stableRoleKind === "skill"}
    <div class="agents-page agents-page--detail">
      <a class="agents-back" href="#/agents" data-agents-back>← Back to Agents</a>
      <ResourceState
        error={skillsHome.error}
        loading={skillsHome.isFetching}
        hasData={Boolean(sharedWorkerSkill)}
        loadingText="Loading Worker skill..."
      >
        {#if sharedWorkerSkill}
          <article class="role-detail" data-agent-detail data-agent-id="panels-worker">
            <header class="agents-detail-head">
              <h1 data-role-name>Worker skill</h1>
              <div class="role-detail-facts">
                <span data-role-structural-id>{sharedWorkerSkill.name}</span>
                <span>Shared role skill · no independent runtime</span>
              </div>
              <p data-role-purpose>{sharedWorkerSkill.description}</p>
            </header>

            <div data-shared-worker-skill>
              <RoleSkillEditor
                heading="Skill"
                skill={sharedWorkerSkill}
                descriptionAriaLabel="Worker skill description"
                bodyAriaLabel="Worker skill Markdown body"
                bodyPlaceholder="Write the complete shared Worker skill body..."
                onSave={saveSharedWorkerSkillField}
              />
            </div>
          </article>
        {/if}
      </ResourceState>
    </div>
  {:else}
    <div class="agents-page agents-page--detail">
      <a class="agents-back" href="#/agents" data-agents-back>← Back to Agents</a>
      <ResourceState
        error={worker.error}
        loading={worker.isFetching}
        hasData={Boolean(worker.data)}
        loadingText="Loading worker..."
      >
        {#if worker.data}
          {@const detail = worker.data}
          {@const manifest = detail.manifest}
          {@const editableSkill = displaySkillForEdit(detail.settings)}
          <article class="role-detail" data-worker-detail data-worker-id={detail.settings.worker_type}>
            <header class="agents-detail-head">
              <h1 data-role-name data-worker-name>{manifest.label || labelize(detail.settings.worker_type)}</h1>
              <div class="role-detail-facts">
                <span data-worker-structural-id>{detail.settings.worker_type}</span>
                <span data-worker-skill-name>{detail.settings.specialist_skill.name}</span>
                <span data-worker-stage-count>{manifest.stages.length} Stages</span>
              </div>
            </header>

            <ManagedLaunchDefaults
              label={manifest.label || labelize(detail.settings.worker_type)}
              value={detail.settings.launch_defaults}
              onSave={(next) => saveWorkerLaunchDefaults(detail.settings, next)}
            />

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

            <div data-worker-skill>
              <RoleSkillEditor
                heading="Specialist skill"
                skill={editableSkill}
                descriptionAriaLabel="Specialist skill description"
                bodyAriaLabel="Specialist skill Markdown body"
                bodyPlaceholder="Write the complete specialist skill body..."
                onSave={(field, raw) => saveWorkerSkillField(detail.settings, field, raw)}
              />
            </div>
          </article>
        {/if}
      </ResourceState>
    </div>
  {/if}
</section>
