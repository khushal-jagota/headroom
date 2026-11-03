<script lang="ts">
  /** A managed file outside an existing conversation pane belongs beside its owner's
   * current conversation. Opening the file leaves that conversation at rest. */
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount } from "svelte";
  import ManagerFrame from "../components/ManagerFrame.svelte";
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import { queries } from "../lib/queryCatalogue";
  import { goalQueries } from "../lib/goals/queries";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import { targetFromPreviewQuery, previewHashHref } from "../lib/filePreview";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { mutateJson } from "../lib/mutate";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { ticketCommandImpact } from "../lib/ticketNavigationImpact";
  import { conversationOwnerImpact } from "../lib/workspaceNavigationImpact";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import { readBackends, type BackendSnapshot, type DeliveredMessage, type OwnerSendBody } from "../lib/conversation/wire";

  let { target: suppliedTarget = null, onClose }: {
    target?: ManagedFileTarget | null;
    onClose?: () => void;
  } = $props();
  const navigation = navigationApplication();
  function parseTarget(): ManagedFileTarget | null {
    const queryIndex = window.location.hash.indexOf("?");
    return targetFromPreviewQuery(new URLSearchParams(queryIndex >= 0 ? window.location.hash.slice(queryIndex + 1) : ""));
  }
  let target = $derived(suppliedTarget ?? parseTarget());
  let ticketId = $derived(target?.kind === "ticket-file" ? target.ticketId : "");
  let managerId = $derived(target?.kind === "manager-file" ? target.managerId : "");
  let goalId = $derived(target?.kind === "goal-file" ? target.goalId : "");
  const ticket = createQuery(() => ({ ...queries.ticket(ticketId), enabled: Boolean(ticketId), retry: false }));
  const manager = createQuery(() => ({ ...queries.managerWorkspace(managerId), enabled: Boolean(managerId), retry: false }));
  const goal = createQuery(() => ({ ...goalQueries.detail(goalId), enabled: Boolean(goalId), retry: false }));
  const ticketStart = createQuery(() => ({ ...queries.ticketConversationStartValues(ticketId), enabled: Boolean(ticketId) }));
  const managerStart = createQuery(() => ({ ...queries.managerConversationStartValues(managerId), enabled: Boolean(managerId) }));
  const goalStart = createQuery(() => ({ ...goalQueries.start(goalId), enabled: Boolean(goalId) }));
  let owner = $derived(ticketId && ticket.data?.id === ticketId ? { kind: "ticket" as const, id: ticketId, title: ticket.data.title, conversationId: ticket.data.conversation_id, locale: ticket.data.locale }
    : managerId && manager.data?.id === managerId ? { kind: "manager" as const, id: managerId, title: manager.data.title, conversationId: manager.data.manager.conversation_id }
    : goalId && goal.data?.id === goalId ? { kind: "goal" as const, id: goalId, title: goal.data.title, conversationId: goal.data.conversation_id }
    : null);
  let ownerError = $derived(ticketId ? ticket.error : managerId ? manager.error : goal.error);
  let ownerLoaded = $derived(ticketId ? ticket.isSuccess : managerId ? manager.isSuccess : goal.isSuccess);
  let startValues = $derived(ticketId ? ticketStart.data : managerId ? managerStart.data : goalStart.data);
  let shownFile = $state<ManagedFileTarget | null>(null);
  let conversationState = $state<ConversationState>("rest");
  let backends = $state<readonly BackendSnapshot[]>([]);
  let sentConversationId = $state<string | null>(null);
  let identity = $derived(target ? previewHashHref(target) : "");
  $effect(() => { identity; shownFile = target; conversationState = "rest"; sentConversationId = null; });
  onMount(() => { void readBackends().then(value => backends = value).catch(() => undefined); });

  function close(): void {
    if (onClose) onClose();
    else if (owner) window.location.hash = workspaceAddress({kind: owner.kind, id: owner.id});
    else window.location.hash = "#/workspace";
  }
  function changeFile(file: ManagedFileTarget | null): void {
    if (!file) close();
    else shownFile = file;
  }
  async function sendMessage(body: OwnerSendBody): Promise<DeliveredMessage> {
    if (!owner || ownerError) throw new Error("The artifact's conversation is unavailable.");
    const recipient = owner;
    const view = navigation?.state.effective;
    const impact = recipient.kind === "ticket"
      ? ticketCommandImpact(view, recipient.id, "conversation")
      : conversationOwnerImpact(view ? navigation!.presentationFor(view) : undefined, recipient.kind, recipient.id);
    const operation = (responseMetadata?: import("../lib/api").FetchOptions["responseMetadata"]) => mutateJson<DeliveredMessage>(
      `/api/${recipient.kind === "ticket" ? "tickets" : recipient.kind === "manager" ? "managers" : "goals"}/${encodeURIComponent(recipient.id)}/conversation/send`,
      { method: "POST", body, responseMetadata });
    const delivered = navigation ? await navigation.runCommand(impact, operation, "send artifact message") : await operation();
    if (delivered.conversation_id !== null) sentConversationId = delivered.conversation_id;
    return delivered;
  }
</script>

<section class="file-preview-route" data-file-preview-route>
  {#if target}
    <ManagerFrame openedFile={shownFile} onOpenFile={changeFile} bind:conversationState>
      <p>{owner?.title ?? "Artifact"}</p>
      {#snippet conversation()}
        {#if owner && !ownerError}
          <div class="artifact-recipient" data-artifact-recipient>Message {owner.title}</div>
          {#key identity}
            <LiveConversation bind:conversationState conversationId={sentConversationId ?? owner.conversationId}
              persistenceKey={`owner:${owner.kind}:${owner.id}`} uploadOwner={owner.kind === "goal" ? null : {kind: owner.kind, id: owner.id}}
              label={owner.title} locale={"locale" in owner ? owner.locale : undefined}
              composerPlaceholder={`Message ${owner.title}...`} {backends} startValues={startValues ?? null}
              senderLabel="owner" {sendMessage} />
          {/key}
        {:else if ownerError || ownerLoaded}
          <p data-artifact-owner-unavailable>The artifact's conversation is unavailable.</p>
          {#if ownerError}<ErrorLine error={ownerError} />{/if}
        {:else}<p>Loading the artifact's conversation…</p>{/if}
      {/snippet}
    </ManagerFrame>
  {:else}<div class="quiet-line">file preview not found</div>{/if}
</section>

<style>
  .file-preview-route { flex:1; min-height:0; display:flex; }
  .artifact-recipient { flex:none; color:var(--text-muted); font-size:var(--type-xs); padding:var(--space-2) var(--space-3); }
</style>
