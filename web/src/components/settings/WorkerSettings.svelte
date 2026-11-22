<script lang="ts">
  import SettingsPage from "./SettingsPage.svelte";
  let { detail }: { detail: string | null } = $props();
</script>
<SettingsPage title="WorkerSettings">{detail}</SettingsPage>
