<script lang="ts">
  import type { SettingsSection } from "../lib/settings/address";
  import { settingsSearch } from "../lib/settings/search.svelte";
  import SettingsPage from "../components/settings/SettingsPage.svelte";
  import SettingsSearchResults from "../components/settings/SettingsSearchResults.svelte";
  import GeneralSettings from "../components/settings/GeneralSettings.svelte";
  import NotificationSettings from "../components/settings/NotificationSettings.svelte";
  import AgentSettings from "../components/settings/AgentSettings.svelte";
  import WorkerSettings from "../components/settings/WorkerSettings.svelte";
  import MachineSettings from "../components/settings/MachineSettings.svelte";
  import BackendSettings from "../components/settings/BackendSettings.svelte";
  import DiagnosticsSettings from "../components/settings/DiagnosticsSettings.svelte";

  /* The page half of Settings. The rail half, with the section list and the search field,
     stands in the workspace rail's place. A search shows its results here. */
  let { section = "general", detail = null }: { section?: SettingsSection; detail?: string | null } = $props();
  let searching = $derived(settingsSearch.query.trim() !== "");
</script>

<div class="settings-screen" data-screen="settings" data-settings-section={section}>
  {#if searching}
    <SettingsPage title="Search"><SettingsSearchResults /></SettingsPage>
  {:else if section === "general"}<GeneralSettings />
  {:else if section === "notifications"}<NotificationSettings />
  {:else if section === "agents"}<AgentSettings {detail} />
  {:else if section === "workers"}<WorkerSettings {detail} />
  {:else if section === "machines"}<MachineSettings />
  {:else if section === "backends"}<BackendSettings />
  {:else}<DiagnosticsSettings />{/if}
</div>
