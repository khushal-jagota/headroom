<script lang="ts">
  import { goalSidebarRows, setGoalSidebarRows } from "../../lib/goals/sidebarRows.svelte";
  import SettingsPage from "./SettingsPage.svelte";
  import SettingsSection from "./SettingsSection.svelte";
  import SettingsSwitchRow from "./SettingsSwitchRow.svelte";
</script>

<SettingsPage title="General">
  <SettingsSection title="Sidebar" first>
    <SettingsSwitchRow label="Show goals in sidebar rows" description="This browser only." rowId="general:goal-sidebar-rows"
      checked={goalSidebarRows.on} save={async next => setGoalSidebarRows(next)} />
  </SettingsSection>
</SettingsPage>
