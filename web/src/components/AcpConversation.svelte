<script lang="ts">
  import { untrack } from "svelte";
  import AcpConversationPane from "./acp/AcpConversationPane.svelte";
  import { createProductionConversationController } from "../lib/acp/productionConversation";

  let {
    employeeId,
    employeeLabel,
    deferInitialAttach = false
  }: {
    employeeId: string;
    employeeLabel: string;
    deferInitialAttach?: boolean;
  } = $props();

  const stableEmployeeId = untrack(() => employeeId);
  const controller = untrack(() => deferInitialAttach)
    ? createProductionConversationController(stableEmployeeId, undefined, true)
    : createProductionConversationController(stableEmployeeId);
</script>

<AcpConversationPane {controller} {employeeLabel} {deferInitialAttach} />
