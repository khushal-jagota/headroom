<script lang="ts">
  /** The Chief of Staff's conversation, wherever the Chief is shown.
   *
   * There is nothing here a Ticket does not also have: the same pane, the same two doors,
   * and a conversation the server records against the Chief the way it records a Ticket's
   * against its row. What it starts as comes from the Chief's own managed settings, and
   * this asks the server what that is rather than guessing, so the backend and the models
   * shown before anybody types are the ones a first message would actually run on.
   *
   * It stays separate from the Agents layout because this component owns the canonical
   * Chief conversation while the route owns roster selection and responsive navigation.
   */
  import { onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";

  import { createQuery } from "@tanstack/svelte-query";
  import GoalsOverview from "./goals/GoalsOverview.svelte";
  import ManagerHeader from "./ManagerHeader.svelte";
  import ManagerFrame from "./ManagerFrame.svelte";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import ManagerChildren from "./ManagerChildren.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";

  import type { ManagedFileTarget } from "../lib/filePreview";
  let { allGoals = false, requestedConversationId = null, openedFile = null, onOpenFile }: { allGoals?: boolean; requestedConversationId?: string | null; openedFile?: ManagedFileTarget | null; onOpenFile?: (file: ManagedFileTarget | null) => void } = $props();
  const navigation = (() => {
    const value = navigationApplication();
    if (!value) throw new Error("Chief conversation requires the application navigation owner");
    return value;
  })();

  let root = $state<HTMLElement | null>(null);
  let displayed = $state.raw<import("../lib/navigation/knowledge").EffectiveNavigationView | null>(null);
  $effect(() => { const view = navigation.selectors.effective; if (view) displayed = view; });
  let chief = $derived(displayed ? navigation.presentationFor(displayed).chief : null);
  $effect(()=>navigation.owner.setDemand('chief',{selected:{kind:'chief',id:'chief'},selected_conversation_id:selectedPastConversationId,windows:[]}));
  onDestroy(()=>navigation.owner.setDemand('chief',undefined));
  let conversationState = $state<ConversationState>("rest");
  let conversationId = $state<string | null>(null);
  let selectedPastConversationId = $state<string | null>(null);
  let shownConversationId = $derived(selectedPastConversationId ?? conversationId);
  let requestedConversationApplied: string | null | undefined;
  $effect(() => {
    if (!chief || requestedConversationApplied === requestedConversationId) return;
    requestedConversationApplied = requestedConversationId;
    selectedPastConversationId = requestedConversationId && requestedConversationId !== chief.conversation_id ? requestedConversationId : null;
    if (requestedConversationId) conversationState = "opened";
  });
  let observedPointer: string | undefined = $state();
  let backends = $state<readonly BackendSnapshot[]>([]);
  // What a conversation for the Chief would start on. It is a query rather than a read on
  // arrival because the owner changes it on the Config screen, and this must not go on
  // showing what the Chief was configured on before they did.
  const startValues = createQuery(() => queries.chiefConversationStartValues());
  const chiefSettings = createQuery(() => queries.chiefSettings());

  $effect(() => {
    if (!chief) return;
    const pointer = JSON.stringify(chief.conversation_id);
    if (pointer !== observedPointer) {
      observedPointer = pointer;
      conversationId = chief.conversation_id;
    }
  });

  async function sendToTheChief(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered=await mutateJson<DeliveredMessage>("/api/chief/conversation/send",{method:"POST",body});
    if(delivered.conversation_id!==null) conversationId=delivered.conversation_id;
    return delivered;
  }
  function saveRecap(recap: string): Promise<unknown> {
    return mutateJson("/api/workers/chief-of-staff/recap", { method: "PUT", body: { recap } });
  }
  async function newConversation():Promise<void> {
    await mutateJson("/api/chief/conversation/reset",{method:"POST"});
    conversationId=null;
  }

  $effect(() => {
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => {
        // The pickers fall back to the value already in force.
      });
  });
</script>

<div class="chief-workspace-host" bind:this={root}>
<ManagerFrame class="chief-workspace" bind:conversationState {openedFile} {onOpenFile}>
  {#if allGoals}
  <GoalsOverview all/>
  {:else}
  <ManagerHeader signal={chief?.conversation_signal} conversationId={chief?.conversation_id}
    recap={chiefSettings.data?.recap} onSaveRecap={saveRecap} recapTarget={{ kind: "chief", id: "chief" }}>
    {#snippet title()}Chief of Staff{/snippet}
  </ManagerHeader>
  <GoalsOverview/>
  <section class="manager-workers" aria-label="Workers">
    <ManagerChildren principal={{ kind: "chief", id: "chief" }} />
  </section>
  {/if}
  {#snippet conversation()}
  <LiveConversation
    ownerLoading={!chief}
    bind:conversationState
    conversationId={shownConversationId}
    bind:selectedPastConversationId
    readOnly={selectedPastConversationId !== null}
    persistenceKey="owner:chief"
    uploadOwner={{kind:"chief",id:"chief"}}
    label="Chief of Staff"
    bind:backends
    startValues={startValues.data ?? null}
    senderLabel="owner"
    sendMessage={sendToTheChief}
    onNewConversation={newConversation}
  />

  {/snippet}
</ManagerFrame>
</div>
<style>
  .chief-workspace-host { display:contents; }
  .manager-workers { margin-top:48px; }
</style>
