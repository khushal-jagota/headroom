<script lang="ts">
  import ConfigRoute from "./ConfigRoute.svelte";
  import NotificationsRoute from "./NotificationsRoute.svelte";
  import ScheduledTasksRoute from "./ScheduledTasksRoute.svelte";
  import { nestedHref, prefixHref, type UtilityHref } from "../lib/utilityAddress";
  let { section = "config", roleKind = "index", roleId, href = prefixHref("#/settings") }: {
    section?: string;
    roleKind?: "index" | "agent" | "skill" | "worker";
    roleId?: string;
    href?: UtilityHref;
  } = $props();
</script>

<section data-settings>
  <header class="settings-title"><h1>Settings</h1></header>
  <div class="settings-head">
    <nav class="settings-sections" aria-label="Settings">
      <a href={href(["config"])} aria-current={section === "config" ? "page" : undefined}>Config</a>
      <a href={href(["scheduled-tasks"])} aria-current={section === "scheduled-tasks" ? "page" : undefined}>Scheduled tasks</a>
      <a href={href(["notifications"])} aria-current={section === "notifications" ? "page" : undefined}>Notifications</a>
    </nav>
  </div>
  {#if section === "config"}<ConfigRoute {roleKind} {roleId} href={nestedHref(href, ["config"])} />
  {:else if section === "scheduled-tasks"}<ScheduledTasksRoute />
  {:else if section === "notifications"}<NotificationsRoute />{/if}
</section>

<style>
  .settings-title {
    padding:var(--space-3) 0;
    border-bottom:var(--border-hairline) solid var(--border-color);
  }
  .settings-title h1 {
    margin:0;
    color:var(--text-strong);
    font:600 var(--type-md) var(--font-ui);
  }
  .settings-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:var(--space-4);
    padding:var(--space-3) 0;
    border-bottom:var(--border-hairline) solid var(--border-color);
  }
  .settings-sections { display:flex; gap:var(--space-4); flex-wrap:wrap; min-width:0; }
  a { color:var(--text-muted); text-decoration:none; padding:var(--space-1) 0; }
  a[aria-current="page"] { color:var(--text-strong); font-weight:500; }
  a:hover { color:var(--text-strong); }
  a:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); }
  @media(max-width:720px) {
    .settings-head { gap:var(--space-2); }
    .settings-sections { gap:var(--space-4); font-size:var(--type-xs); }
  }
</style>
