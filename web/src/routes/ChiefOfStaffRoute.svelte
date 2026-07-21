<script lang="ts">
  import AcpConversation from "../components/AcpConversation.svelte";

  const entityId = "agent_panels_chief_of_staff";
</script>

<section class="chief-chat-page" data-screen="chief" data-chief-of-staff-route>
  <header class="chief-chat-head">
    <h1>Chief of Staff</h1>
  </header>
  <div class="chief-chat-shell">
    <AcpConversation employeeId={entityId} employeeLabel="Chief of Staff" />
  </div>
</section>
