<script lang="ts">
  import { onDestroy } from "svelte";
  import ChatPanel from "../components/ChatPanel.svelte";
  import ChiefNeutralPane from "../components/ChiefNeutralPane.svelte";
  import { relayChief, retryRelayChiefMeta } from "../lib/capabilities";
  import { resourceCatalogue } from "../lib/resourceCatalogue";

  const entityId = "agent_panels_chief_of_staff";
  // The legacy gateway-status resource is only meaningful on the legacy (disabled) path; the
  // neutral pane has its own connection meaning. Open it lazily so the neutral branch never
  // subscribes it.
  let chatStatus: ReturnType<typeof resourceCatalogue.chatGatewayStatus> | null = null;

  function legacyChatStatus(): ReturnType<typeof resourceCatalogue.chatGatewayStatus> {
    if (chatStatus === null) chatStatus = resourceCatalogue.chatGatewayStatus(entityId);
    return chatStatus;
  }

  onDestroy(() => {
    chatStatus?.dispose();
  });
</script>

<section class="chief-chat-page" data-screen="chief" data-chief-of-staff-route>
  <header class="chief-chat-head">
    <h1>Chief of Staff</h1>
  </header>
  <div class="chief-chat-shell">
    {#if $relayChief === "enabled"}
      <ChiefNeutralPane {entityId} label="Chief of Staff" />
    {:else if $relayChief === "disabled"}
      {@const status = legacyChatStatus()}
      <ChatPanel
        {entityId}
        available={status.data?.available ?? true}
        label="Chief of Staff"
      />
    {:else if $relayChief === "error"}
      <div class="chief-chat-placeholder" data-chief-meta-error>
        <p>Could not load Chief of Staff.</p>
        <button type="button" data-chief-meta-retry onclick={() => void retryRelayChiefMeta()}>
          Retry
        </button>
      </div>
    {:else}
      <div class="chief-chat-placeholder" data-chief-meta-loading></div>
    {/if}
  </div>
</section>
