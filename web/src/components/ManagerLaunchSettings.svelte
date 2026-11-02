<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import type { EmployeeConfigurationSnapshot } from "../lib/types";
  import ManagedLaunchDefaults from "./ManagedLaunchDefaults.svelte";
  import ResourceState from "./ResourceState.svelte";

  let { managerId, backendLocked = false }: {managerId?: string; backendLocked?: boolean} = $props();
  const settings = createQuery(() => managerId ? queries.managerLaunchConfiguration(managerId) : queries.managerLaunchDefaults());
  function save(next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson(managerId ? `/api/managers/${encodeURIComponent(managerId)}/launch-defaults` : "/api/managers/launch-defaults", {method: "PUT", body: next});
  }
</script>

<ResourceState shape="inline" error={settings.error} loading={settings.isFetching} hasData={Boolean(settings.data)} loadingText="Loading Manager model…">
  {#if settings.data}
    <ManagedLaunchDefaults label="Manager" heading={managerId ? "Model" : "Manager launch defaults"}
      description={managerId ? "Applies to the next turn." : "Applies to new Managers."}
      value={settings.data} {backendLocked} onSave={save} />
  {/if}
</ResourceState>
