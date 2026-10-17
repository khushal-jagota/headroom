<script lang="ts">
  import { untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import "../lib/config/config.css";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ManagedLaunchDefaults from "../components/ManagedLaunchDefaults.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import RoleSkillEditor from "../components/RoleSkillEditor.svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import type { ConfigSelection } from "../lib/shellRouting";
  import type { WorkerTypeManifest } from "../lib/lifecycle";
  import { errorMessage, labelize } from "../lib/ui";
  import type {
    ChiefManagementSettings,
    EmployeeConfigurationSnapshot,
    ManagedSkill,
    StageOwnershipMode,
    WorkerManagementDetail,
    WorkerManagementSettings,
    WorkerTypeRecord
  } from "../lib/types";

  const roleSkillProfiles: Record<string, {
    label: string;
    fact: string;
    descriptionAriaLabel: string;
    bodyAriaLabel: string;
    bodyPlaceholder: string;
  }> = {
    "panels-sprint-item-supervisor": {
      label: "Sprint Item supervisor",
      fact: "Role skill",
      descriptionAriaLabel: "Sprint Item supervisor skill description",
      bodyAriaLabel: "Sprint Item supervisor skill Markdown body",
      bodyPlaceholder: "Write the complete Sprint Item supervisor skill body..."
    },
    "panels-worker": {
      label: "Worker skill",
      fact: "Shared role skill",
      descriptionAriaLabel: "Worker skill description",
      bodyAriaLabel: "Worker skill Markdown body",
      bodyPlaceholder: "Write the complete shared Worker skill body..."
    }
  };

  let { roleKind, roleId, baseHref = "#/settings/config" }: ConfigSelection & { baseHref?: string } = $props();
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
  const supportedWorkerTypes = createQuery(() => ({
    ...queries.supportedWorkerTypeManifests(),
    enabled: stableRoleKind === "index"
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
  let indexedWorkerTypes = $derived(
    new Map((supportedWorkerTypes.data?.worker_types || []).map((item) => [item.worker_type, item]))
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

  const ownerOptions: Array<{ value: StageOwnershipMode; label: string }> = [
    { value: "worker", label: "worker" },
    { value: "user", label: "user" }
  ];

  // What the row shows while its save is in flight, and what went wrong if it failed.
  // A finished save clears the pending value, so the table falls back to what the
  // refetched record says. A failed save clears it too, which reverts the control.
  let pendingOwners = $state<Record<string, StageOwnershipMode>>({});
  let ownerSaving = $state<Record<string, boolean>>({});
  let ownerErrors = $state<Record<string, unknown>>({});

  function stageOwnerValue(
    stage: { id: string; ownership_mode: StageOwnershipMode | null }
  ): string {
    return pendingOwners[stage.id] || stage.ownership_mode || "";
  }

  // Owner saves run one at a time. Each one reads the whole record and writes it
  // back, so a second Stage changed while the first is in flight must not read the
  // record from before that write and post the old value over it.
  let ownerWrites: Promise<void> = Promise.resolve();

  // A Worker type is coherent only as a whole record, so there is no write for one
  // Stage's owner. Read the stored record, change this Stage, and post it back.
  async function saveStageOwner(
    workerType: string,
    stage: string,
    ownershipMode: StageOwnershipMode
  ): Promise<void> {
    pendingOwners = { ...pendingOwners, [stage]: ownershipMode };
    ownerSaving = { ...ownerSaving, [stage]: true };
    const { [stage]: _cleared, ...remainingErrors } = ownerErrors;
    ownerErrors = remainingErrors;
    const write = ownerWrites.then(async () => {
      const record = await fetchJson<WorkerTypeRecord>(
        `/api/worker-types/${encodeURIComponent(workerType)}`
      );
      await mutateJson<WorkerTypeRecord>("/api/worker-types", {
        method: "POST",
        body: {
          ...record,
          stages: record.stages.map((candidate) =>
            candidate.id === stage ? { ...candidate, ownership_mode: ownershipMode } : candidate
          )
        }
      });
    });
    // A refused write stops that row, not the queue behind it.
    ownerWrites = write.catch(() => undefined);
    try {
      await write;
    } catch (error) {
      ownerErrors = { ...ownerErrors, [stage]: error };
    } finally {
      const { [stage]: _settled, ...remainingPending } = pendingOwners;
      pendingOwners = remainingPending;
      ownerSaving = { ...ownerSaving, [stage]: false };
    }
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
        loadingText="Loading configuration..."
      >
        {#if workers.data}
          <section class="agents-index-section" data-agents-section>
            <h2 class="agents-section-title">Agents</h2>
            <div class="agents-destination-list">
              <a
                class="agents-destination"
                href={`${baseHref}/chief-of-staff`}
                data-agent-destination
              >
                <span class="agents-destination-copy">
                  <span class="agents-destination-name" data-destination-name>
                    {workers.data.chief_of_staff.label}
                  </span>
                  <span class="agents-destination-description" data-destination-description>
                    Launch defaults and role skill
                  </span>
                </span>
                <span class="agents-destination-arrow" aria-hidden="true">→</span>
              </a>
              {#if supervisorSkill}
                <a
                  class="agents-destination"
                  href={`${baseHref}/sprint-item-supervisor`}
                  data-agent-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>
                      Sprint Item supervisor
                    </span>
                    <span class="agents-destination-description" data-destination-description>
                      Role skill
                    </span>
                  </span>
                  <span class="agents-destination-arrow" aria-hidden="true">→</span>
                </a>
              {/if}
              {#if sharedWorkerSkill}
                <a
                  class="agents-destination"
                  href={`${baseHref}/worker-skill`}
                  data-agent-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>Worker skill</span>
                    <span class="agents-destination-description" data-destination-description>
                      Shared role skill
                    </span>
                  </span>
                  <span class="agents-destination-arrow" aria-hidden="true">→</span>
                </a>
              {/if}
            </div>
          </section>

          <section class="agents-index-section" data-workers-section>
            <h2 class="agents-section-title">Workers</h2>
            {#if supportedWorkerTypes.error}<ErrorLine error={supportedWorkerTypes.error} />{/if}
            <div class="agents-destination-list" data-workers-list>
              {#each sortedWorkers as item}
                {@const manifest = indexedWorkerTypes.get(item.worker_type)}
                <a
                  class="agents-destination"
                  href={`${baseHref}/workers/${encodeURIComponent(item.worker_type)}`}
                  data-worker-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>{item.label}</span>
                    <span class="agents-destination-description" data-destination-description>
                      {#if manifest}
                        {manifest.stages.length} {manifest.stages.length === 1 ? "Stage" : "Stages"}
                      {:else}
                        {supportedWorkerTypes.isFetching ? "Loading Stages…" : "Stages unavailable"}
                      {/if}
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
      <a class="agents-back" href={baseHref} data-agents-back>← Back to Config</a>
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
                <span class="config-identity" aria-hidden="true" data-role-structural-id>chief_of_staff</span>
                <span class="config-identity" aria-hidden="true" data-role-skill-name>{chief.skill.name}</span>
                <span>Agent · no ticket lifecycle</span>
              </div>
              <p class="config-identity" aria-hidden="true" data-role-purpose>{chief.skill.description}</p>
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
      <a class="agents-back" href={baseHref} data-agents-back>← Back to Config</a>
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
                <span class="config-identity" aria-hidden="true" data-role-structural-id>{roleSkill.name}</span>
                <span>{roleSkillProfile.fact}</span>
              </div>
              <p class="config-identity" aria-hidden="true" data-role-purpose>{roleSkill.description}</p>
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
      <a class="agents-back" href={baseHref} data-agents-back>← Back to Config</a>
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
                <span class="config-identity" aria-hidden="true" data-worker-structural-id>{detail.settings.worker_type}</span>
                <span class="config-identity" aria-hidden="true" data-worker-skill-name>{detail.settings.specialist_skill.name}</span>
                <span data-worker-stage-count>{manifest.stages.length} Stages</span>
              </div>
            </header>

            <ManagedLaunchDefaults
              label={manifest.label || labelize(detail.settings.worker_type)}
              value={detail.settings.launch_defaults}
              onSave={(next) => saveWorkerLaunchDefaults(detail.settings, next)}
            />

            <section class="worker-stage-table-shell" aria-labelledby="config-stages-title">
              <header class="worker-stages-head"><h2 id="config-stages-title">Stages</h2></header>
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
                        <span class="worker-stage-id config-identity" aria-hidden="true">{stage.id}</span>
                      </td>
                      <td class="worker-stage-gated-field" data-gated-field>{gatedFieldLabel(manifest, stage.gating_field)}</td>
                      <td data-stage-owner={stage.is_terminal ? "" : stageOwnerValue(stage)}>
                        {#if stage.is_terminal}
                          <span class="worker-readonly-owner" data-terminal-owner>terminal</span>
                        {:else}
                          <select
                            class="worker-owner-select"
                            aria-label={`Owner for ${stage.label}`}
                            data-stage-owner-select
                            data-saving={ownerSaving[stage.id] ? "true" : "false"}
                            value={stageOwnerValue(stage)}
                            onchange={(event) => void saveStageOwner(
                              detail.settings.worker_type,
                              stage.id,
                              event.currentTarget.value as StageOwnershipMode
                            )}
                          >
                            {#each ownerOptions as option}
                              <option value={option.value}>{option.label}</option>
                            {/each}
                          </select>
                          {#if ownerErrors[stage.id]}
                            <div class="worker-row-error" data-stage-owner-error>
                              {errorMessage(ownerErrors[stage.id])}
                            </div>
                          {/if}
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </section>

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
