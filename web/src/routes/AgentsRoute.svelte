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
    WorkerManagementSettings,
    WorkerManagementSummary
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
  const manifests = createQuery(() => ({
    ...queries.workerTypeManifests(),
    enabled: stableRoleKind === "index"
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

  let indexedManifests = $derived(
    new Map((manifests.data?.worker_types || []).map((item) => [item.worker_type, item]))
  );
  let sharedWorkerSkill = $derived(
    skillsHome.data?.skills.find((skill) => skill.name === "panels-worker")
  );

  function stageCount(workerSummary: WorkerManagementSummary): number {
    return indexedManifests.get(workerSummary.worker_type)?.stages.length || 0;
  }

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

  function launchValue(value: string | null): string {
    return value || "default";
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
        <p>Every configurable role. Agents run at the top level; Workers run one Ticket at a time.</p>
      </header>
      <ResourceState
        {...resourceStateForQueries(workers, manifests, skillsHome)}
        loadingText="Loading agents..."
      >
        {#if workers.data}
          <section class="agents-index-section" data-agents-section>
            <header class="agents-section-head">
              <h2>Agents</h2>
              <span>top-level · no ticket lifecycle</span>
            </header>
            <div class="agent-card-list">
              <article class="agent-card" data-agent-card data-agent-id="chief_of_staff">
                <div class="agent-card-head">
                  <h3 data-agent-label>{workers.data.chief_of_staff.label}</h3>
                  <span>Agent</span>
                </div>
                <p data-agent-purpose>{workers.data.chief_of_staff.skill.description}</p>
                <div class="agent-card-foot">
                  <span class="agent-chip">backend <b>{launchValue(workers.data.chief_of_staff.launch_defaults.employee_backend)}</b></span>
                  <span class="agent-chip">model <b>{launchValue(workers.data.chief_of_staff.launch_defaults.employee_launch_model)}</b></span>
                  <span class="agent-chip">reasoning <b>{launchValue(workers.data.chief_of_staff.launch_defaults.employee_launch_reasoning_effort)}</b></span>
                  <span class="agent-chip">skill <b data-agent-skill-name>{workers.data.chief_of_staff.skill.name}</b></span>
                  <a class="agent-configure-link" href="#/agents/chief-of-staff" data-agent-configure>
                    Configure &amp; edit skill →
                  </a>
                </div>
              </article>
              {#if sharedWorkerSkill}
                <article class="agent-card" data-agent-card data-agent-id="panels-worker">
                  <div class="agent-card-head">
                    <h3 data-agent-label>Worker skill</h3>
                    <span>Shared role skill</span>
                  </div>
                  <p data-agent-purpose>{sharedWorkerSkill.description}</p>
                  <div class="agent-card-foot">
                    <span class="agent-chip">skill <b data-agent-skill-name>{sharedWorkerSkill.name}</b></span>
                    <a class="agent-configure-link" href="#/agents/worker-skill" data-worker-skill-configure>
                      Configure &amp; edit skill →
                    </a>
                  </div>
                </article>
              {/if}
            </div>
          </section>

          <section class="agents-index-section" data-workers-section>
            <header class="agents-section-head">
              <h2>Workers</h2>
              <span>ticket worker types · stage lifecycle</span>
            </header>
            <div class="workers-list" data-workers-list>
              {#each workers.data.workers as item}
                <a
                  class="workers-row"
                  href={`#/agents/workers/${encodeURIComponent(item.worker_type)}`}
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
