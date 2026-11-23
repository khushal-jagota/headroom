<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import ResourceState from "../ResourceState.svelte";
  import SkeletonRegion from "../SkeletonRegion.svelte";
  import SettingsModelRow from "./SettingsModelRow.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import SettingsSkillSections from "./SettingsSkillSections.svelte";
  import { mutateJson } from "../../lib/mutate";
  import { queries } from "../../lib/queryCatalogue";
  import { resourceStateForQueries } from "../../lib/resourceStateForQueries";
  import { settingsHref, type SettingsAgentId } from "../../lib/settings/address";
  import { launchModelText } from "../../lib/settings/launchModel";
  import { saveSkillField } from "../../lib/skills";
  import { errorMessage } from "../../lib/ui";
  import type { ChiefManagementSettings, EmployeeConfigurationSnapshot } from "../../lib/types";

  /* Agents: the Chief of Staff, the Manager role and the skill every Worker shares. The list
     shows each one's model; a detail page holds the model picker, then the skill. The
     Manager and Worker skill rows appear only while their skill rows exist. */
  let { detail }: { detail: string | null } = $props();
  let id = $derived(detail as SettingsAgentId | null);

  const workers = createQuery(() => ({ ...queries.workers(), enabled: id === null || id === "chief-of-staff" }));
  const skillsHome = createQuery(() => ({ ...queries.skillsHome(), enabled: id !== "chief-of-staff" }));
  const managerDefaults = createQuery(() => ({ ...queries.managerLaunchDefaults(), enabled: id === null || id === "manager" }));
  const backends = createQuery(() => ({ ...queries.backends(), enabled: id === null }));
  const drivers = createQuery(() => ({ ...queries.drivers(), enabled: id === null }));

  const profiles = {
    manager: {
      name: "Manager", skill: "panels-manager",
      descriptionAriaLabel: "Manager skill description",
      bodyAriaLabel: "Manager skill Markdown body",
      bodyPlaceholder: "Write the complete Manager skill body..."
    },
    "worker-skill": {
      name: "Worker skill", skill: "panels-worker",
      descriptionAriaLabel: "Worker skill description",
      bodyAriaLabel: "Worker skill Markdown body",
      bodyPlaceholder: "Write the complete shared Worker skill body..."
    }
  } as const;

  let skills = $derived(new Map((skillsHome.data?.skills ?? []).map((skill) => [skill.name, skill])));
  let chief = $derived(workers.data?.chief_of_staff);
  let title = $derived(id === null ? "Agents" : id === "chief-of-staff" ? (chief?.label ?? "Chief of Staff") : profiles[id].name);

  function modelText(value: EmployeeConfigurationSnapshot | undefined): string {
    return value ? launchModelText(value, backends.data ?? [], drivers.data?.drivers) : "";
  }

  function saveChiefLaunchDefaults(next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson<ChiefManagementSettings>("/api/workers/chief-of-staff/launch-defaults", { method: "PUT", body: next })
      .then((saved) => saved.launch_defaults);
  }

  function saveManagerLaunchDefaults(next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson<EmployeeConfigurationSnapshot>("/api/managers/launch-defaults", { method: "PUT", body: next });
  }
</script>

{#snippet value(text: string)}<span class="agent-settings-value">{text}</span>{/snippet}

<SettingsPage {title} parent={id === null ? undefined : { label: "Agents", href: settingsHref("agents") }}>
  {#if id === null}
    <ResourceState shape="rows" {...resourceStateForQueries(workers, skillsHome)} loadingText="Loading agents…">
      {#if chief}
        <SettingsSection first>
          <SettingsRow label={chief.label} href={settingsHref("agents", "chief-of-staff")} rowId="agents:chief-of-staff">
            {#snippet control()}{@render value(modelText(chief.launch_defaults))}{/snippet}
          </SettingsRow>
          {#if skills.has("panels-manager")}
            <SettingsRow label="Manager" href={settingsHref("agents", "manager")} rowId="agents:manager">
              {#snippet control()}{@render value(modelText(managerDefaults.data))}{/snippet}
            </SettingsRow>
          {/if}
          {#if skills.has("panels-worker")}
            <SettingsRow label="Worker skill" href={settingsHref("agents", "worker-skill")} rowId="agents:worker-skill">
              {#snippet control()}{@render value("Shared by every Worker")}{/snippet}
            </SettingsRow>
          {/if}
        </SettingsSection>
      {/if}
    </ResourceState>
  {:else if id === "chief-of-staff"}
    <ResourceState shape="document" error={workers.error} loading={workers.isFetching} hasData={Boolean(chief)} loadingText="Loading agent…">
      {#if chief}
        <SettingsSection first>
          <SettingsModelRow rowId="agents:chief-of-staff:model" value={chief.launch_defaults} onSave={saveChiefLaunchDefaults} />
        </SettingsSection>
        <SettingsSkillSections skill={chief.skill} rowIdPrefix="agents:chief-of-staff"
          descriptionAriaLabel="Chief skill description" bodyAriaLabel="Chief skill Markdown body"
          bodyPlaceholder="Write the complete Chief skill body..."
          onSave={(field, raw) => saveSkillField(chief.skill.name, field, raw)} />
      {/if}
    </ResourceState>
  {:else}
    {@const profile = profiles[id]}
    {@const skill = skills.get(profile.skill)}
    {#key id}
      {#if id === "manager"}
        <SettingsSection first>
          {#if managerDefaults.data}
            <SettingsModelRow description="Applies to new Managers." rowId="agents:manager:model"
              value={managerDefaults.data} onSave={saveManagerLaunchDefaults} />
          {:else}
            <SettingsRow label="Model" description="Applies to new Managers." rowId="agents:manager:model"
              error={managerDefaults.error ? errorMessage(managerDefaults.error) : null}>
              {#snippet control()}<SkeletonRegion shape="inline" loading={managerDefaults.isFetching} loadingText="Loading Manager model…" />{/snippet}
            </SettingsRow>
          {/if}
        </SettingsSection>
      {/if}
      <ResourceState shape="document" error={skillsHome.error} loading={skillsHome.isFetching} hasData={Boolean(skill)} loadingText="Loading skill…">
        {#if skill}
          <SettingsSkillSections {skill} first={id !== "manager"} rowIdPrefix={`agents:${id}`}
            descriptionAriaLabel={profile.descriptionAriaLabel} bodyAriaLabel={profile.bodyAriaLabel}
            bodyPlaceholder={profile.bodyPlaceholder}
            onSave={(field, raw) => saveSkillField(skill.name, field, raw)} />
        {/if}
      </ResourceState>
    {/key}
  {/if}
</SettingsPage>

<style>
  .agent-settings-value { flex:none; color:var(--tone-secondary); font-size:var(--type-row); font-variant-numeric:tabular-nums; white-space:nowrap; }
</style>
