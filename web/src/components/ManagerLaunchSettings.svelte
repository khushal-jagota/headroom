<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { mutateJson } from "../lib/mutate";
  import type { EmployeeConfigurationSnapshot } from "../lib/types";
  import ManagedLaunchDefaults from "./ManagedLaunchDefaults.svelte";
  import ResourceState from "./ResourceState.svelte";

  const settings = createQuery(() => queries.managerLaunchDefaults());
  function save(next: EmployeeConfigurationSnapshot): Promise<EmployeeConfigurationSnapshot> {
    return mutateJson("/api/managers/launch-defaults", {method: "PUT", body: next});
  }
</script>

<ResourceState shape="inline" error={settings.error} loading={settings.isFetching} hasData={Boolean(settings.data)} loadingText="Loading Manager model…">
  {#if settings.data}
    <ManagedLaunchDefaults label="Manager" description="Applies to new Managers."
      value={settings.data} onSave={save} />
  {/if}
</ResourceState>
