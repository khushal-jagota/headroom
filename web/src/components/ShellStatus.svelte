<script lang="ts">
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import { tick, onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { runtimeCountImpact } from "../lib/reviewNavigationImpact";
  import { shellRuntimeDomMatches } from "../lib/shellDom";
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
    connectionState
  }: {
    connectionState: ConnectionStatus;
  } = $props();

  const navigation = (() => { const value = navigationApplication(); if (!value) throw new Error("Shell requires the application navigation owner"); return value; })();
  const observer = navigation.registerRenderBoundary(`shell-runtime:${crypto.randomUUID()}`,impact => {
    const view=navigation.state.effective;return runtimeCountImpact(impact,view ? navigation.presentationFor(view) : undefined);
  });
  onDestroy(() => navigation.disposeRenderBoundary(observer));
  let root = $state<HTMLElement | null>(null);
  let displayed = $state.raw<import("../lib/navigation/knowledge").EffectiveNavigationView | null>(null);
  $effect(() => { const view = navigation.selectors.effective; if (view) displayed = view; });
  let runningWorkerCount = $derived(displayed ? navigation.presentationFor(displayed).review.runningConversationCount : null);
  $effect(() => {
    const effective = displayed;
    const generation = navigation.state.generation;
    const element = root;
    const count = runningWorkerCount;
    const label = statusLabels[statusState];
    if (!effective?.runtimeCurrent || !element) return;
    const publication = effective.publication;
    const observation = observer.observeSelectors(publication,generation);
    let obsolete = false;
    void tick().then(() => {
      if (!obsolete && element.isConnected && navigation.state.generation === generation
        && navigation.selectors.effective === effective && shellRuntimeDomMatches(element,count,label)) observer.acknowledgeRender(publication,observation);
    });
    return () => { obsolete = true; };
  });

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
  bind:this={root}
  class="shell-status"
  role="status"
  aria-live="polite"
  aria-label={`${statusLabels[statusState]}.${runningWorkerCount === null ? "" : ` ${runningWorkerCount} working.`}`}
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
        <SkeletonRegion shape="inline" loading={!vps.isError && !vps.data} loadingText="Loading VPS status">
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
        {/if}
        </SkeletonRegion>
      </section>
    {/if}
  </div>
  {#if runningWorkerCount !== null}
  <span class="shell-presence" data-shell-presence>
    {#if runningWorkerCount > 0}
      <span class="shell-presence-spin" aria-hidden="true"></span>
    {/if}
    {runningWorkerCount} working
  </span>
  {/if}
</div>
