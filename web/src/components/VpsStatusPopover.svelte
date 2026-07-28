<script lang="ts">
  import { fetchJson } from "../lib/api";
  import type { ConnectionStatus } from "../lib/changeStream";
  import type { VpsStatusSection, VpsStatusSnapshot } from "../lib/types";

  let { connectionState }: { connectionState: ConnectionStatus } = $props();

  let isOpen = $state(false);
  let isLoading = $state(false);
  let error = $state<string | null>(null);
  let snapshot = $state<VpsStatusSnapshot | null>(null);

  const connectionLabels = {
    connected: "Connected",
    reconnecting: "Reconnecting"
  } as const;

  const stateLabels = {
    healthy: "Healthy",
    warning: "Warning",
    critical: "Critical",
    unavailable: "Unavailable",
    review_needed: "Review needed"
  } as const;

  function statusRows(value: VpsStatusSnapshot): Array<[string, VpsStatusSection]> {
    return [
      ["Environment", value.environment],
      ["App", value.app],
      ["Backup", value.backup],
      ["Disk", value.disk],
      ["Resources", value.resources]
    ];
  }

  async function refresh(): Promise<void> {
    isLoading = true;
    error = null;
    try {
      snapshot = await fetchJson<VpsStatusSnapshot>("/api/vps-status");
    } catch {
      error = "Status is unavailable";
    } finally {
      isLoading = false;
    }
  }

  function toggle(): void {
    isOpen = !isOpen;
    if (isOpen) void refresh();
  }
</script>

<div class="vps-status" data-vps-status>
  <button
    class="vps-status-trigger"
    type="button"
    aria-expanded={isOpen}
    aria-controls="vps-status-popover"
    aria-label={`${connectionLabels[connectionState]}. Show VPS status`}
    data-connection-status
    data-state={connectionState}
    onclick={toggle}
  >
    <span class="vps-status-mark" aria-hidden="true"></span>
    <span class="vps-status-label" role="status" aria-live="polite">
      {connectionLabels[connectionState]}
    </span>
  </button>
  {#if isOpen}
    <section id="vps-status-popover" class="vps-status-popover" aria-label="Panels status">
      <div class="vps-status-heading">
        <strong>{snapshot ? stateLabels[snapshot.overall_state] : "Status"}</strong>
        <button type="button" class="vps-status-refresh" onclick={() => void refresh()} disabled={isLoading}>
          {isLoading ? "Refreshing" : "Refresh"}
        </button>
      </div>
      {#if error}
        <p class="vps-status-error" data-vps-status-error>{error}</p>
      {:else if snapshot}
        <dl class="vps-status-list" data-vps-status-content>
          {#each statusRows(snapshot) as [label, section]}
            <div data-state={section.state}>
              <dt>{label}</dt>
              <dd>{stateLabels[section.state]} · {section.summary}</dd>
            </div>
          {/each}
        </dl>
      {:else}
        <p class="vps-status-loading">Loading status</p>
      {/if}
    </section>
  {/if}
</div>
