<script lang="ts">
  import { onDestroy } from "svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import { resourceCatalogue } from "../lib/resourceCatalogue";

  const entityId = "agent_panels_chief_of_staff";
  const chatStatus = resourceCatalogue.chatGatewayStatus(entityId);

  onDestroy(() => {
    chatStatus.dispose();
  });
</script>

<section class="chief-chat-page" data-screen="chief" data-chief-of-staff-route>
  <header class="chief-chat-head">
    <h1>Chief of Staff</h1>
  </header>
  <div class="chief-chat-shell">
    <ChatPanel
      {entityId}
      available={chatStatus.data?.available ?? true}
      label="Chief of Staff"
    />
  </div>
</section>
