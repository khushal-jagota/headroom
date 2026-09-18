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
  import { labelize } from "../lib/ui";
  import type {
    ChiefManagementSettings,
    EmployeeConfigurationSnapshot,
    ManagedSkill,
    WorkerManagementDetail,
    WorkerManagementSettings
  } from "../lib/types";

  type RoleKind = "index" | "agent" | "skill" | "worker";

  const roleSkillProfiles: Record<string, {
    label: string;
    fact: string;
    descriptionAriaLabel: string;
    bodyAriaLabel: string;
    bodyPlaceholder: string;
  }> = {
    "panels-sprint-item-supervisor": {
      label: "Sprint Item supervisor",
      fact: "Agent role skill · one identity per Sprint Item",
      descriptionAriaLabel: "Sprint Item supervisor skill description",
      bodyAriaLabel: "Sprint Item supervisor skill Markdown body",
      bodyPlaceholder: "Write the complete Sprint Item supervisor skill body..."
    },
    "panels-worker": {
      label: "Worker skill",
      fact: "Shared role skill · no independent runtime",
      descriptionAriaLabel: "Worker skill description",
      bodyAriaLabel: "Worker skill Markdown body",
      bodyPlaceholder: "Write the complete shared Worker skill body..."
    }
  };

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


  let indexedSkills = $derived(
    new Map((skillsHome.data?.skills || []).map((skill) => [skill.name, skill]))
  );
  let sortedWorkers = $derived(
    [...(workers.data?.workers || [])].sort(
      (left, right) =>
        left.label.localeCompare(right.label, undefined, { sensitivity: "base" }) ||
        left.worker_type.localeCompare(right.worker_type)
    )
  );
  let sharedWorkerSkill = $derived(indexedSkills.get("panels-worker"));
  let supervisorSkill = $derived(indexedSkills.get("panels-sprint-item-supervisor"));
  let roleSkill = $derived(stableRoleId ? indexedSkills.get(stableRoleId) : undefined);
  let roleSkillProfile = $derived(
    stableRoleId ? roleSkillProfiles[stableRoleId] : undefined
  );

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

  async function saveRoleSkillField(
    skillName: string,
    field: "description" | "markdown_body",
    raw: string
  ): Promise<void> {
    await mutateJson<ManagedSkill>(`/api/skills/${encodeURIComponent(skillName)}`, {
      method: "PATCH",
      body: { [field]: raw }
    });
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

  function gatedFieldLabel(manifest: WorkerTypeManifest, fieldId: string | null): string {
    if (!fieldId) return "";
    return manifest.fields.find((field) => field.id === fieldId)?.label || labelize(fieldId);
  }

</script>

<section
  class="agents-screen"
  data-screen="config"
  data-role-kind={stableRoleKind}
  data-role-id={stableRoleId || undefined}
>
  {#if stableRoleKind === "index"}
    <div class="agents-page agents-page--index">
      <header class="agents-page-head">
        <h1>Config</h1>
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
                href="#/config/chief-of-staff"
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
              {#if supervisorSkill}
                <a
                  class="agents-destination"
                  href="#/config/sprint-item-supervisor"
                  data-agent-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>
                      Sprint Item supervisor
                    </span>
                    <span class="agents-destination-description" data-destination-description>
                      {supervisorSkill.description}
                    </span>
                  </span>
                  <span class="agents-destination-arrow" aria-hidden="true">→</span>
                </a>
              {/if}
              {#if sharedWorkerSkill}
                <a
                  class="agents-destination"
                  href="#/config/worker-skill"
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
              {#each sortedWorkers as item}
                <a
                  class="agents-destination"
                  href={`#/config/workers/${encodeURIComponent(item.worker_type)}`}
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
      <a class="agents-back" href="#/config" data-agents-back>← Back to Config</a>
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
      <a class="agents-back" href="#/config" data-agents-back>← Back to Config</a>
      <ResourceState
        error={skillsHome.error}
        loading={skillsHome.isFetching}
        hasData={Boolean(roleSkill && roleSkillProfile)}
        loadingText="Loading role skill..."
      >
        {#if roleSkill && roleSkillProfile}
          <article class="role-detail" data-agent-detail data-agent-id={roleSkill.name}>
            <header class="agents-detail-head">
              <h1 data-role-name>{roleSkillProfile.label}</h1>
              <div class="role-detail-facts">
                <span data-role-structural-id>{roleSkill.name}</span>
                <span>{roleSkillProfile.fact}</span>
              </div>
              <p data-role-purpose>{roleSkill.description}</p>
            </header>

            <div
              data-role-skill
              data-shared-worker-skill={roleSkill.name === "panels-worker" ? "" : undefined}
            >
              <RoleSkillEditor
                heading="Skill"
                skill={roleSkill}
                descriptionAriaLabel={roleSkillProfile.descriptionAriaLabel}
                bodyAriaLabel={roleSkillProfile.bodyAriaLabel}
                bodyPlaceholder={roleSkillProfile.bodyPlaceholder}
                onSave={(field, raw) => saveRoleSkillField(roleSkill.name, field, raw)}
              />
            </div>
          </article>
        {/if}
      </ResourceState>
    </div>
  {:else}
    <div class="agents-page agents-page--detail">
      <a class="agents-back" href="#/config" data-agents-back>← Back to Config</a>
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
                    <th>Owner</th>
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
                      <td data-stage-owner={stage.ownership_mode || ""}>
                        {#if stage.is_terminal}
                          <span class="worker-readonly-owner" data-terminal-owner>terminal</span>
                        {:else}
                          <span class="worker-readonly-owner">{stage.ownership_mode}</span>
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
