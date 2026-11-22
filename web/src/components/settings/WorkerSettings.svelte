<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import Button from "../Button.svelte";
  import ErrorLine from "../ErrorLine.svelte";
  import ResourceState from "../ResourceState.svelte";
  import SkeletonRegion from "../SkeletonRegion.svelte";
  import SettingsMarkdownWell from "./SettingsMarkdownWell.svelte";
  import SettingsModelRow from "./SettingsModelRow.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  import SettingsRow from "./SettingsRow.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import SettingsWorkflowSection from "./SettingsWorkflowSection.svelte";
  import { mutateJson } from "../../lib/mutate";
  import { queries } from "../../lib/queryCatalogue";
  import { settingsHref } from "../../lib/settings/address";
  import { launchModelText } from "../../lib/settings/launchModel";
  import { labelize } from "../../lib/ui";
  import type { EmployeeConfigurationSnapshot, WorkerManagementSettings } from "../../lib/types";

  /* Workers: one row per Worker type with its stage count and model. A Worker type's page
     holds its model picker, its workflow preset and the specialist skill Panels generates
     from that workflow. */
  let { detail }: { detail: string | null } = $props();

  const workers = createQuery(() => queries.workers());
  const manifests = createQuery(() => ({ ...queries.supportedWorkerTypeManifests(), enabled: detail === null }));
  const backends = createQuery(() => ({ ...queries.backends(), enabled: detail === null }));
  const drivers = createQuery(() => ({ ...queries.drivers(), enabled: detail === null }));
  const worker = createQuery(() => ({ ...queries.worker(detail ?? ""), enabled: detail !== null }));

  let sortedWorkers = $derived(
    [...(workers.data?.workers ?? [])].sort(
      (left, right) =>
        left.label.localeCompare(right.label, undefined, { sensitivity: "base" }) ||
        left.worker_type.localeCompare(right.worker_type)
    )
  );
  let manifestsByType = $derived(new Map((manifests.data?.worker_types ?? []).map((item) => [item.worker_type, item])));
  let workerDetail = $derived(detail !== null && worker.data?.settings.worker_type === detail ? worker.data : undefined);
  let name = $derived.by(() => {
    if (detail === null) return "Workers";
    return workerDetail?.manifest.label
      || workers.data?.workers.find((item) => item.worker_type === detail)?.label
      || labelize(detail);
  });
  let skillOpen = $state(false);
  // Each Worker type's page opens with its generated skill hidden.
  $effect.pre(() => { void detail; skillOpen = false; });

  function saveLaunchDefaults(workerType: string, next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson<WorkerManagementSettings>(`/api/workers/${encodeURIComponent(workerType)}/launch-defaults`, { method: "PUT", body: next })
      .then((saved) => saved.launch_defaults);
  }
</script>

<SettingsPage title={name} parent={detail === null ? undefined : { label: "Workers", href: settingsHref("workers") }}>
  {#if detail === null}
    <ResourceState shape="rows" error={workers.error} loading={workers.isFetching} hasData={Boolean(workers.data)} loadingText="Loading Workers…">
      {#if manifests.error}<ErrorLine error={manifests.error} />{/if}
      {#if workers.data}
        <SettingsSection first>
          {#each sortedWorkers as item (item.worker_type)}
            {@const manifest = manifestsByType.get(item.worker_type)}
            <SettingsRow label={item.label} href={settingsHref("workers", item.worker_type)} rowId={`workers:${item.worker_type}`}>
              {#snippet description()}
                <SkeletonRegion shape="inline" loading={!manifest && manifests.isFetching} loadingText="Loading stages…">
                  {#if manifest}
                    {manifest.stages.length} {manifest.stages.length === 1 ? "stage" : "stages"}
                  {:else if !manifests.isFetching}Stages unavailable{/if}
                </SkeletonRegion>
              {/snippet}
              {#snippet control()}
                <span class="worker-settings-value">{launchModelText(item.launch_defaults, backends.data ?? [], drivers.data?.drivers)}</span>
              {/snippet}
            </SettingsRow>
          {/each}
        </SettingsSection>
      {/if}
    </ResourceState>
  {:else}
    {#key detail}
      <ResourceState shape="document" error={worker.error} loading={worker.isFetching} hasData={Boolean(workerDetail)} loadingText="Loading Worker…">
        {#if workerDetail}
          {@const settings = workerDetail.settings}
          {@const skill = settings.candidate_specialist_skill || settings.specialist_skill}
          <SettingsSection first>
            <SettingsModelRow description="New Tickets of this type start with it." rowId={`workers:${settings.worker_type}:model`}
              value={settings.launch_defaults} onSave={(next) => saveLaunchDefaults(settings.worker_type, next)} />
          </SettingsSection>
          <SettingsWorkflowSection workerType={settings.worker_type} {name} />
          <SettingsSection>
            <SettingsRow label="Generated specialist skill" description="Read-only. Panels builds it from the workflow.">
              {#snippet control()}
                <Button variant="ghost" aria-expanded={skillOpen} aria-controls="worker-settings-skill" onclick={() => (skillOpen = !skillOpen)}>{skillOpen ? "Hide" : "Show"}</Button>
              {/snippet}
              {#snippet after()}
                {#if skillOpen}
                  <div class="worker-settings-skill" id="worker-settings-skill" data-worker-skill>
                    {#if skill.description}<p class="worker-settings-skill-description">{skill.description}</p>{/if}
                    <SettingsMarkdownWell markdown={skill.markdown_body} />
                  </div>
                {/if}
              {/snippet}
            </SettingsRow>
          </SettingsSection>
        {/if}
      </ResourceState>
    {/key}
  {/if}
</SettingsPage>

<style>
  .worker-settings-value { flex:none; color:var(--tone-secondary); font-size:var(--type-row); font-variant-numeric:tabular-nums; white-space:nowrap; }
  .worker-settings-skill { padding-bottom:var(--space-4); }
  .worker-settings-skill-description { margin:0; font-size:var(--type-small); color:var(--tone-tertiary); }
</style>
