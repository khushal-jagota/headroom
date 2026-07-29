<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import type { ConnectionStatus } from "../lib/changeStream";
  import {
    deploymentStatusExpiryDelay,
    resolveDeploymentStatus,
    type VisibleDeploymentState
  } from "../lib/deploymentStatus";
  import { queries } from "../lib/queryCatalogue";
  import type {
    DeploymentOutcome,
    VpsBackupMetric,
    VpsPercentageMetric,
    VpsStatusSummary
  } from "../lib/types";

  let { connectionState }: { connectionState: ConnectionStatus } = $props();

  let isOpen = $state(false);
  let clock = $state(Date.now());

  const deployment = createQuery(() => queries.deploymentStatus());
  const vps = createQuery(() => ({
    ...queries.vpsStatusSummary(),
    enabled: isOpen
  }));

  const statusState = $derived(
    resolveDeploymentStatus(deployment.data, connectionState, clock)
  );

  const statusLabels: Record<VisibleDeploymentState, string> = {
    connected: "Connected",
    reconnecting: "Reconnecting",
    preparing: "Preparing",
    restarting: "Restarting",
    back_up: "Back up",
    problem: "Problem"
  };

  const metricStateLabels = {
    healthy: "Healthy",
    warning: "Warning",
    critical: "Critical",
    unavailable: "Unavailable"
  } as const;

  const outcomeLabels: Record<DeploymentOutcome, string> = {
    succeeded: "Succeeded",
    failed: "Failed",
    rolled_back: "Rolled back"
  };

  $effect(() => {
    const status = deployment.data;
    const now = Date.now();
    clock = now;
    const delay = deploymentStatusExpiryDelay(status, now);
    if (delay === null) return;
    const timer = window.setTimeout(() => {
      clock = Date.now();
    }, delay);
    return () => window.clearTimeout(timer);
  });

  function shortSha(sha: string | null): string {
    return sha ? sha.slice(0, 8) : "Unavailable";
  }

  function percentage(metric: VpsPercentageMetric): string {
    if (metric.used_percent === null) return "Unavailable";
    return `${Math.round(metric.used_percent)}% · ${metricStateLabels[metric.state]}`;
  }

  function backupAge(metric: VpsBackupMetric): string {
    if (metric.age_seconds === null) return "Unavailable";
    const seconds = Math.max(0, Math.round(metric.age_seconds));
    if (seconds < 60) return `${seconds}s ago · ${metricStateLabels[metric.state]}`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago · ${metricStateLabels[metric.state]}`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ago · ${metricStateLabels[metric.state]}`;
    return `${Math.floor(hours / 24)}d ago · ${metricStateLabels[metric.state]}`;
  }

  function outcome(summary: VpsStatusSummary): string | null {
    return summary.deployment.outcome
      ? outcomeLabels[summary.deployment.outcome]
      : null;
  }
</script>

<div class="vps-status" data-vps-status>
  <button
    class="vps-status-trigger"
    type="button"
    aria-expanded={isOpen}
    aria-controls="vps-status-popover"
    aria-label={`${statusLabels[statusState]}. Show VPS status`}
    data-connection-status
    data-state={connectionState}
    data-connection-state={connectionState}
    data-status-state={statusState}
    onclick={() => (isOpen = !isOpen)}
  >
    <span class="vps-status-mark" aria-hidden="true"></span>
    <span class="vps-status-label" role="status" aria-live="polite">
      {statusLabels[statusState]}
    </span>
  </button>
  {#if isOpen}
    <section id="vps-status-popover" class="vps-status-popover" aria-label="VPS status">
      <div class="vps-status-heading">
        <strong>VPS status</strong>
        <button
          type="button"
          class="vps-status-refresh"
          onclick={() => void vps.refetch()}
          disabled={vps.isFetching}
        >
          {vps.isFetching ? "Refreshing" : "Refresh"}
        </button>
      </div>
      {#if vps.isError}
        <p class="vps-status-error" data-vps-status-error>VPS status is unavailable</p>
      {:else if vps.data}
        <dl class="vps-status-list" data-vps-status-content>
          <div>
            <dt>Deployed commit</dt>
            <dd>{shortSha(vps.data.deployed_sha)}</dd>
          </div>
          {#if outcome(vps.data)}
            <div data-state={vps.data.deployment.outcome === "succeeded" ? "healthy" : "critical"}>
              <dt>Deployment</dt>
              <dd>{outcome(vps.data)}</dd>
            </div>
          {/if}
          <div data-state={vps.data.cpu.state}>
            <dt>CPU</dt>
            <dd>{percentage(vps.data.cpu)}</dd>
          </div>
          <div data-state={vps.data.ram.state}>
            <dt>RAM</dt>
            <dd>{percentage(vps.data.ram)}</dd>
          </div>
          <div data-state={vps.data.disk.state}>
            <dt>Disk</dt>
            <dd>{percentage(vps.data.disk)}</dd>
          </div>
          <div data-state={vps.data.backup.state}>
            <dt>Latest verified backup</dt>
            <dd>{backupAge(vps.data.backup)}</dd>
          </div>
        </dl>
      {:else}
        <p class="vps-status-loading">Loading VPS status</p>
      {/if}
    </section>
  {/if}
</div>
