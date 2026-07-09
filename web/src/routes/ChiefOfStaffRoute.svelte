<script lang="ts">
  import { onDestroy } from "svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import { fetchJson } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { GatewayStatus } from "../lib/types";

  const entityId = "agent_panels_chief_of_staff";
  const chatStatus = resource<GatewayStatus>(`chat-status:${entityId}`, (signal) =>
    fetchJson(`/api/chat/${entityId}/status`, { signal })
  );

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
