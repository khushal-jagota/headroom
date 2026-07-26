<script lang="ts">
  /** The Chief of Staff's conversation, wherever the Chief is shown.
   *
   * There is nothing here a Ticket does not also have: the same pane, the same two doors,
   * and a conversation the server records against the Chief the way it records a Ticket's
   * against its row. What it starts as comes from the Chief's own managed settings.
   *
   * It is a component rather than part of a route because the Chief appears in two places
   * — its own screen and the Workspace desk — and both must be the same conversation.
   */
  import { onMount } from "svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson } from "../lib/mutate";
  import { readBackends, type BackendSnapshot } from "../lib/conversation/wire";

  type ChiefConversation = { conversation_id: string | null };

  let conversationId = $state<string | null>(null);
  let backends = $state<readonly BackendSnapshot[]>([]);

  async function startTheConversation(): Promise<string | null> {
    const started = await mutateJson<ChiefConversation>("/api/chief/conversation", {
      method: "POST"
    });
    conversationId = started.conversation_id;
    return started.conversation_id;
  }

  async function newConversation(): Promise<void> {
    await mutateJson("/api/chief/conversation/reset", { method: "POST" });
    conversationId = null;
  }

  onMount(() => {
    void fetchJson<ChiefConversation>("/api/chief/conversation")
      .then((current) => (conversationId = current.conversation_id))
      .catch(() => {
        // Nothing recorded yet reads the same as nothing to show: the first message starts
        // one, which is the path a Ticket takes too.
      });
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => {
        // The pickers fall back to the value already in force.
      });
  });
</script>

<LiveConversation
  {conversationId}
  label="Chief of Staff"
  {backends}
  senderLabel="owner"
  onStartConversation={startTheConversation}
  onNewConversation={newConversation}
/>
