<script lang="ts">
  import ConfigRoute from "./ConfigRoute.svelte";
  import NotificationsRoute from "./NotificationsRoute.svelte";
  import ScheduledTasksRoute from "./ScheduledTasksRoute.svelte";
  let { section = "config", roleKind = "index", roleId }: {
    section?: string;
    roleKind?: "index" | "agent" | "skill" | "worker";
    roleId?: string;
  } = $props();
</script>

<section data-settings>
  <nav class="settings-sections" aria-label="Settings">
    <a href="#/settings/config" aria-current={section === "config" ? "page" : undefined}>Config</a>
    <a href="#/settings/scheduled-tasks" aria-current={section === "scheduled-tasks" ? "page" : undefined}>Scheduled tasks</a>
    <a href="#/settings/notifications" aria-current={section === "notifications" ? "page" : undefined}>Notifications</a>
  </nav>
  {#if section === "config"}<ConfigRoute {roleKind} {roleId} />
  {:else if section === "scheduled-tasks"}<ScheduledTasksRoute />
  {:else if section === "notifications"}<NotificationsRoute />{/if}
</section>

<style>
  .settings-sections { display:flex; gap:var(--space-4); flex-wrap:wrap; padding:var(--space-3) var(--space-4); }
  a { color:var(--text-muted); text-decoration:none; }
  a[aria-current="page"] { color:var(--text-strong); }
  a:focus-visible { outline:var(--border-hairline) solid var(--text-strong); outline-offset:var(--space-1); }
</style>
