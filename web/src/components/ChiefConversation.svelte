<script lang="ts">
  /** The Chief of Staff's conversation, wherever the Chief is shown.
   *
   * There is nothing here a Ticket does not also have: the same pane, the same two doors,
   * and a conversation the server records against the Chief the way it records a Ticket's
   * against its row. What it starts as comes from the Chief's own managed settings, and
   * this asks the server what that is rather than guessing, so the backend and the models
   * shown before anybody types are the ones a first message would actually run on.
   *
   * It is a component rather than part of a route because the Chief appears in two places
   * — its own screen and the Workspace desk — and both must be the same conversation.
   */
  import { onMount } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";

  type ChiefConversation = { conversation_id: string | null };

  let conversationId = $state<string | null>(null);
  let backends = $state<readonly BackendSnapshot[]>([]);
  // What a conversation for the Chief would start on. It is a query rather than a read on
  // arrival because the owner changes it on the Agents screen, and this must not go on
  // showing what the Chief was configured on before they did.
  const startValues = createQuery(() => queries.chiefConversationStartValues());

  async function sendToTheChief(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered = await mutateJson<DeliveredMessage>("/api/chief/conversation/send", {
      method: "POST",
      body
    });
    if (delivered.conversation_id !== null) conversationId = delivered.conversation_id;
    return delivered;
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
  startValues={startValues.data ?? null}
  senderLabel="owner"
  sendMessage={sendToTheChief}
  onNewConversation={newConversation}
/>
