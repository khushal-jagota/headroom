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

  let {
    connectionState,
    runningWorkerCount
  }: {
    connectionState: ConnectionStatus;
    runningWorkerCount: number;
  } = $props();

  let clock = $state(Date.now());
  let isOpen = $state(false);
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

  function deploymentOutcome(summary: VpsStatusSummary): string {
    return summary.deployment.outcome
      ? outcomeLabels[summary.deployment.outcome]
      : "Unavailable";
  }

  function deploymentOutcomeState(summary: VpsStatusSummary): string {
    if (!summary.deployment.outcome) return "unavailable";
    return summary.deployment.outcome === "succeeded" ? "healthy" : "critical";
  }
</script>

<div
  class="shell-status"
  role="status"
  aria-live="polite"
  aria-label={`${statusLabels[statusState]}. ${runningWorkerCount} working.`}
  data-shell-status
>
  <div class="vps-status" data-vps-status>
    <button
      class="shell-connection shell-connection-trigger"
      type="button"
      aria-expanded={isOpen}
      aria-controls="shell-vps-status-popover"
      aria-label={`${statusLabels[statusState]}. Show deployment and VPS status`}
      data-connection-status
      data-state={connectionState}
      data-connection-state={connectionState}
      data-status-state={statusState}
      onclick={() => (isOpen = !isOpen)}
    >
      <span class="shell-connection-mark" aria-hidden="true"></span>
      <span class="shell-connection-label" role="status" aria-live="polite">
        {statusLabels[statusState]}
      </span>
    </button>
    {#if isOpen}
      <section
        id="shell-vps-status-popover"
        class="vps-status-popover"
        aria-label="Deployment and VPS status"
      >
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
            <div data-state={deploymentOutcomeState(vps.data)}>
              <dt>Deployment</dt>
              <dd>{deploymentOutcome(vps.data)}</dd>
            </div>
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
  <span class="shell-presence" data-shell-presence>
    {#if runningWorkerCount > 0}
      <span class="shell-presence-spin" aria-hidden="true"></span>
    {/if}
    {runningWorkerCount} working
  </span>
</div>
