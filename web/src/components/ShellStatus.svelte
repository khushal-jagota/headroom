<script lang="ts">
  import type { ConnectionStatus } from "../lib/changeStream";

  let {
    connectionState,
    runningWorkerCount
  }: {
    connectionState: ConnectionStatus;
    runningWorkerCount: number;
  } = $props();

  const connectionLabels = {
    connected: "Connected",
    reconnecting: "Reconnecting"
  } as const;
</script>

<div
  class="shell-status"
  role="status"
  aria-live="polite"
  aria-label={`${connectionLabels[connectionState]}. ${runningWorkerCount} working.`}
  data-shell-status
  data-connection-status
  data-state={connectionState}
>
  <span class="shell-connection">
    <span class="shell-connection-mark" aria-hidden="true"></span>
    {connectionLabels[connectionState]}
  </span>
  <span class="shell-presence" data-shell-presence>
    {#if runningWorkerCount > 0}
      <span class="shell-presence-spin" aria-hidden="true"></span>
    {/if}
    {runningWorkerCount} working
  </span>
</div>
