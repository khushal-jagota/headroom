<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import MachineChoice from "../machines/MachineChoice.svelte";
  import BackendMachineSettings from "./BackendMachineSettings.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  const machines = createQuery(() => queries.machines());
  let selectedMachineId = $state<string | null>(null);
  const machine = $derived(machines.data?.machines.find(item => selectedMachineId === null ? item.is_home : item.machine_id === selectedMachineId));
</script>

{#snippet machineChoice()}
  {#if machine}<MachineChoice machines={machines.data?.machines ?? []} value={machine.machine_id} onchange={value => selectedMachineId = value} />{/if}
{/snippet}
{#if machine}
  {#key machine.machine_id}
    <BackendMachineSettings machineId={machine.machine_id} machineName={machine.name} {machineChoice} />
  {/key}
{:else}
  <SettingsPage title="Backends">
    {#if machines.error}<p role="alert">Could not read machines.</p>{:else}<p role="status">Loading machines…</p>{/if}
  </SettingsPage>
{/if}
