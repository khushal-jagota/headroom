<script lang="ts">
  import PresetWorkflowEditor from "../components/PresetWorkflowEditor.svelte";
  import SkeletonRegion from "../components/SkeletonRegion.svelte";
  import { untrack } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import "../lib/config/config.css";
  import ErrorLine from "../components/ErrorLine.svelte";
  import ManagerLaunchSettings from "../components/ManagerLaunchSettings.svelte";
  import ManagedLaunchDefaults from "../components/ManagedLaunchDefaults.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import RoleSkillEditor from "../components/RoleSkillEditor.svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { resourceStateForQueries } from "../lib/resourceStateForQueries";
  import { saveSkillField } from "../lib/skills";
  import type { ConfigSelection } from "../lib/shellRouting";
  import { prefixHref, type UtilityHref } from "../lib/utilityAddress";
  import { labelize } from "../lib/ui";
  import type {
    ChiefManagementSettings,
    EmployeeConfigurationSnapshot,
    ManagedSkill,
    WorkerManagementDetail,
    WorkerManagementSettings
  } from "../lib/types";

  const roleSkillProfiles: Record<string, {
    label: string;
    fact: string;
    descriptionAriaLabel: string;
    bodyAriaLabel: string;
    bodyPlaceholder: string;
  }> = {
    "panels-manager": {
      label: "Manager",
      fact: "Role skill",
      descriptionAriaLabel: "Manager skill description",
      bodyAriaLabel: "Manager skill Markdown body",
      bodyPlaceholder: "Write the complete Manager skill body..."
    },
    "panels-worker": {
      label: "Worker skill",
      fact: "Shared role skill",
      descriptionAriaLabel: "Worker skill description",
      bodyAriaLabel: "Worker skill Markdown body",
      bodyPlaceholder: "Write the complete shared Worker skill body..."
    }
  };

  let { roleKind, roleId, href = prefixHref("#/settings/config") }:
    ConfigSelection & { href?: UtilityHref } = $props();
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
  // The canonical role can use the retained row until the owner applies the skill manifest.
  let managerSkill = $derived(indexedSkills.get("panels-manager"));
  let roleSkill = $derived(stableRoleId === "panels-manager" ? managerSkill : stableRoleId ? indexedSkills.get(stableRoleId) : undefined);
  let roleSkillProfile = $derived(
    stableRoleId ? roleSkillProfiles[stableRoleId] : undefined
  );

  function displaySkillForEdit(settings: WorkerManagementSettings): ManagedSkill {
    return settings.candidate_specialist_skill || settings.specialist_skill;
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


</script>

<section
  class="agents-screen"
  data-screen="config"
  data-role-kind={stableRoleKind}
  data-role-id={stableRoleId || undefined}
>
  {#if stableRoleKind === "index"}
    <div class="agents-page agents-page--index">
      <ResourceState shape="document"
        {...resourceStateForQueries(workers, skillsHome)}
        loadingText="Loading configuration..."
      >
        {#if workers.data}
          <section class="agents-index-section" data-agents-section>
            <h2 class="agents-section-title">Agents</h2>
            <div class="agents-destination-list">
              <a
                class="agents-destination"
                href={href(["chief-of-staff"])}
                data-agent-destination
              >
                <span class="agents-destination-copy">
                  <span class="agents-destination-name" data-destination-name>
                    {workers.data.chief_of_staff.label}
                  </span>
                </span>
                <span class="agents-destination-arrow" aria-hidden="true">→</span>
              </a>
              {#if managerSkill}
                <a
                  class="agents-destination"
                  href={href(["manager"])}
                  data-agent-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>
                      Manager
                    </span>
                  </span>
                  <span class="agents-destination-arrow" aria-hidden="true">→</span>
                </a>
              {/if}
              {#if sharedWorkerSkill}
                <a
                  class="agents-destination"
                  href={href(["worker-skill"])}
                  data-agent-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>Worker skill</span>
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
                  href={href(["workers", encodeURIComponent(item.worker_type)])}
                  data-worker-destination
                >
                  <span class="agents-destination-copy">
                    <span class="agents-destination-name" data-destination-name>{item.label}</span>
                    <span class="agents-destination-description" data-destination-description>
                      <SkeletonRegion shape="inline" loading={!manifest && supportedWorkerTypes.isFetching} loadingText="Loading Stages…">
                      {#if manifest}
                        {manifest.stages.length} {manifest.stages.length === 1 ? "Stage" : "Stages"}
                      {:else}
                        {#if !supportedWorkerTypes.isFetching}Stages unavailable{/if}
                      {/if}
                      </SkeletonRegion>
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
      <a class="agents-back" href={href([])} data-agents-back>← Back to Config</a>
      <ResourceState shape="document"
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
                onSave={(field, raw) => saveSkillField(chief.skill.name, field, raw)}
              />
            </div>
          </article>
        {/if}
      </ResourceState>
    </div>
  {:else if stableRoleKind === "skill"}
    <div class="agents-page agents-page--detail">
      <a class="agents-back" href={href([])} data-agents-back>← Back to Config</a>
      <ResourceState shape="document"
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

            {#if stableRoleId === "panels-manager"}<ManagerLaunchSettings />{/if}

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
                onSave={(field, raw) => saveSkillField(roleSkill.name, field, raw)}
              />
            </div>
          </article>
        {/if}
      </ResourceState>
    </div>
  {:else}
    <div class="agents-page agents-page--detail">
      <a class="agents-back" href={href([])} data-agents-back>← Back to Config</a>
      <ResourceState shape="document"
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

            <PresetWorkflowEditor workerType={detail.settings.worker_type} />

            <details class="generated-skill" data-worker-skill>
              <summary>Generated specialist skill</summary>
              <p>{editableSkill.description}</p>
              <pre>{editableSkill.markdown_body}</pre>
            </details>
          </article>
        {/if}
      </ResourceState>
    </div>
  {/if}
</section>

<style>
  .generated-skill { margin-top: var(--space-7); }
  .generated-skill summary { cursor: pointer; color: var(--text-muted); font-size: var(--type-sm); }
  .generated-skill p { margin-top: var(--space-3); color: var(--text-muted); }
  .generated-skill pre { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: var(--space-3); padding: var(--space-4); background: var(--surface-recessed); border-radius: var(--radius-sm); font: var(--type-xs)/1.6 var(--font-mono); }
</style>
