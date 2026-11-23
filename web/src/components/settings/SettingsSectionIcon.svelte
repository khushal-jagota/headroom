<script lang="ts">
  import type { SettingsSection } from "../../lib/settings/address";
  let { section }: { section: SettingsSection } = $props();
</script>

<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if section === "general"}<path d="M4 8h9.5M18.5 8H20M4 16h1.5M10.5 16H20" /><circle cx="16" cy="8" r="2.25" /><circle cx="8" cy="16" r="2.25" />
  {:else if section === "notifications"}<path d="M6.25 16.5v-5.75a5.75 5.75 0 0 1 11.5 0v5.75l1.5 2H4.75l1.5-2Z" /><path d="M10 21h4" />
  {:else if section === "agents"}<circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.9-3.6 3.6-5.5 7-5.5s6.1 1.9 7 5.5" />
  {:else if section === "workers"}<rect x="4" y="4.75" width="16" height="14.5" rx="2.75" /><path d="M8 10h8M8 14h5" />
  {:else if section === "machines"}<rect x="3.75" y="5" width="16.5" height="11" rx="2" /><path d="M9 19.75h6" />
  {:else if section === "backends"}<path d="m5 7.5 4.5 4.5L5 16.5M12 17h7" />
  {:else}<path d="M3.75 13h3.5l2.25-6 4.5 11 2.25-5h4" />{/if}
</svg>
