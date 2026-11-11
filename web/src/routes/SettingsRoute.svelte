<script lang="ts">
  import {goalSidebarRows,setGoalSidebarRows} from "../lib/goals/sidebarRows.svelte";
  import ManagerHeader from "../components/ManagerHeader.svelte";
  import VpsPage from "../components/vps/VpsPage.svelte";
  import BackendSettings from "../components/conversation/BackendSettings.svelte";
  import ConfigRoute from "./ConfigRoute.svelte";
  import NotificationsRoute from "./NotificationsRoute.svelte";
  import { nestedHref, prefixHref, type UtilityHref } from "../lib/utilityAddress";
  let { section = "config", roleKind = "index", roleId, href = prefixHref("#/settings") }: {
    section?: string;
    roleKind?: "index" | "agent" | "skill" | "worker";
    roleId?: string;
    href?: UtilityHref;
  } = $props();
</script>

<section data-settings>
  <ManagerHeader>{#snippet title()}Settings{/snippet}</ManagerHeader>
  <div class="settings-head">
    <nav class="settings-sections segmented" aria-label="Settings">
      <a class="segmented-option" href={href(["config"])} aria-current={section === "config" ? "page" : undefined}>Config</a>
      <a class="segmented-option" href={href(["backends"])} aria-current={section === "backends" ? "page" : undefined}>Backends</a>
      <a class="segmented-option" href={href(["notifications"])} aria-current={section === "notifications" ? "page" : undefined}>Notifications</a>
      <a class="segmented-option" href={href(["vps"])} aria-current={section === "vps" ? "page" : undefined}>VPS</a>
    </nav>
  </div>
  {#if section === "config" && roleKind === "index"}<label class="sidebar-goal-setting"><input type="checkbox" checked={goalSidebarRows.on} onchange={(event)=>setGoalSidebarRows(event.currentTarget.checked)}/>Show goals in sidebar rows</label>{/if}
  {#if section === "config"}<ConfigRoute {roleKind} {roleId} href={nestedHref(href, ["config"])} />
  {:else if section === "backends"}<BackendSettings />
  {:else if section === "notifications"}<NotificationsRoute />
  <!-- VPS lives in Settings (owner); #/vps still opens the same page on its own. -->
  {:else if section === "vps"}<div class="settings-vps"><VpsPage embedded /></div>{/if}
</section>

<style>
  [data-settings] { width:100%; max-width:var(--page-measure); margin-inline:auto; padding-top:var(--page-top); }
  [data-settings] :global(.agents-page) { max-width:none; padding-inline:0; }
  .settings-vps :global(.screen[data-screen="vps"]) { padding-inline:0; padding-top:var(--page-block-gap); }
  .sidebar-goal-setting { display:flex; align-items:center; gap:var(--space-2); margin-top:var(--space-5); font-size:var(--type-body); color:var(--tone-primary); }
  .sidebar-goal-setting input { accent-color:var(--accent-bright); }
  .settings-head { margin-top:var(--space-5); }
  .settings-sections { display:flex; flex-wrap:wrap; gap:var(--space-1); min-width:0; }
  .settings-sections a { text-decoration:none; }
</style>
