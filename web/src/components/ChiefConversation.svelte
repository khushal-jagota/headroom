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
  import { tick, onDestroy } from "svelte";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { conversationOwnerImpact } from "../lib/workspaceNavigationImpact";
  import { actualMark, expectedMark } from "../lib/conversationSignalDom";
  import { createQuery } from "@tanstack/svelte-query";
  import OrchestratorHeader from "./OrchestratorHeader.svelte";
  import OrchestratorWorkspace from "./OrchestratorWorkspace.svelte";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import ErrorLine from "./ErrorLine.svelte";
  import OrchestratorChildren from "./OrchestratorChildren.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";

  let { showRequestErrorLine = true }: { showRequestErrorLine?: boolean } = $props();
  const navigation = (() => {
    const value = navigationApplication();
    if (!value) throw new Error("Chief conversation requires the application navigation owner");
    return value;
  })();
  const observer = navigation.registerRenderBoundary(`chief-conversation:${crypto.randomUUID()}`,impact => {
    if(impact.kind==='chief') return impact.fields==='*' || impact.fields.includes('conversation_id');
    return impact.kind==='conversation' && impact.id===chief?.conversation_id && (impact.fields==='*' || impact.fields.includes('attention'));
  });
  onDestroy(() => navigation.disposeRenderBoundary(observer));
  let root = $state<HTMLElement | null>(null);
  let displayed = $state.raw<import("../lib/navigation/knowledge").EffectiveNavigationView | null>(null);
  $effect(() => { const view = navigation.selectors.effective; if (view) displayed = view; });
  let chief = $derived(displayed ? navigation.presentationFor(displayed).chief : null);
  $effect(()=>navigation.owner.setDemand('chief',{selected:{kind:'chief',id:'chief'},windows:[]}));
  onDestroy(()=>navigation.owner.setDemand('chief',undefined));
  let conversationState = $state<ConversationState>("rest");
  let conversationId = $state<string | null>(null);
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
  $effect(() => {
    const effective = displayed;
    const generation = navigation.state.generation;
    const element = root;
    const source = chief;
    const localPointer = conversationId;
    if (!effective?.runtimeCurrent || !element || !source || source.conversation_id !== localPointer) return;
    const publication = effective.publication;
    const observation = observer.observeSelectors(publication,generation);
    let obsolete = false;
    void tick().then(() => {
      if (obsolete || !element.isConnected || navigation.selectors.effective !== effective
        || navigation.state.generation !== generation || conversationId !== source.conversation_id) return;
      const pane = element.querySelector('[data-conversation-pane]');
      if (pane?.getAttribute('data-conversation-id') !== (source.conversation_id ?? '')) return;
      const region=element.querySelector('.sprint-workspace-title');
      if(JSON.stringify(region ? actualMark(region) : [null,null]) !== JSON.stringify(expectedMark(source.conversation_signal,source.conversation_id))) return;
      observer.acknowledgeRender(publication,observation);
    });
    return () => { obsolete = true; };
  });

  async function sendToTheChief(body: OwnerSendBody): Promise<DeliveredMessage> {
    const view=navigation.state.effective;
    const delivered=await navigation.runCommand(conversationOwnerImpact(view ? navigation.presentationFor(view) : undefined,'chief','chief'),responseMetadata =>
      mutateJson<DeliveredMessage>("/api/chief/conversation/send",{method:"POST",body,responseMetadata}),"send Chief message");
    if(delivered.conversation_id!==null) conversationId=delivered.conversation_id;
    return delivered;
  }
  function saveRecap(recap: string): Promise<unknown> {
    return mutateJson("/api/workers/chief-of-staff/recap", { method: "PUT", body: { recap } });
  }
  async function newConversation():Promise<void> {
    const view=navigation.state.effective;
    await navigation.runCommand(conversationOwnerImpact(view ? navigation.presentationFor(view) : undefined,'chief','chief'),responseMetadata =>
      mutateJson("/api/chief/conversation/reset",{method:"POST",responseMetadata}),"reset Chief conversation");
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
<OrchestratorWorkspace class="chief-workspace" bind:conversationState>
  <OrchestratorHeader signal={chief?.conversation_signal} conversationId={chief?.conversation_id}
    recap={chiefSettings.data?.recap} onSaveRecap={saveRecap} recapTarget={{ kind: "chief", id: "chief" }}>
    {#snippet title()}Chief of Staff{/snippet}
  </OrchestratorHeader>
  <section class="orchestrator-workers" aria-label="Workers">
    <OrchestratorChildren navigationErrorHandledByParent principal={{ kind: "chief", id: "chief" }} />
  </section>
  {#snippet conversation()}
{#if navigation.state.error}
  <div class="chief-conversation-resource" data-chief-conversation-error>
    {#if showRequestErrorLine}<ErrorLine error={navigation.state.error} />{/if}
    <button
      type="button"
      class="button button--quiet"
      data-chief-conversation-retry
      onclick={() => navigation.owner.recover().catch(() => undefined)}
    >
      Retry
    </button>
  </div>
{:else if !chief}
  <div class="quiet-line chief-conversation-resource" data-chief-conversation-loading>
    Loading conversation...
  </div>
{:else}
  <LiveConversation
    bind:conversationState
    {conversationId}
    persistenceKey="owner:chief"
    label="Chief of Staff"
    bind:backends
    startValues={startValues.data ?? null}
    senderLabel="owner"
    sendMessage={sendToTheChief}
    onNewConversation={newConversation}
  />
{/if}

  {/snippet}
</OrchestratorWorkspace>
</div>
<style>
  .chief-workspace-host { display:contents; }
  .orchestrator-workers { margin-top:var(--space-5); }
</style>
