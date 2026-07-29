<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import type { ConnectionStatus } from "../lib/changeStream";
  import {
    deploymentStatusExpiryDelay,
    resolveDeploymentStatus,
    type VisibleDeploymentState
  } from "../lib/deploymentStatus";
  import { queries } from "../lib/queryCatalogue";

  let {
    connectionState,
    runningWorkerCount
  }: {
    connectionState: ConnectionStatus;
    runningWorkerCount: number;
  } = $props();

  let clock = $state(Date.now());
  const deployment = createQuery(() => queries.deploymentStatus());
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
</script>

<div
  class="shell-status"
  role="status"
  aria-live="polite"
  aria-label={`${statusLabels[statusState]}. ${runningWorkerCount} working.`}
  data-shell-status
  data-connection-status
  data-state={connectionState}
  data-connection-state={connectionState}
  data-status-state={statusState}
>
  <span class="shell-connection">
    <span class="shell-connection-mark" aria-hidden="true"></span>
    {statusLabels[statusState]}
  </span>
  <span class="shell-presence" data-shell-presence>
    {#if runningWorkerCount > 0}
      <span class="shell-presence-spin" aria-hidden="true"></span>
    {/if}
    {runningWorkerCount} working
  </span>
</div>
