<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import type { ConversationController } from "../../lib/acp/conversationController";
  import type { ConversationSnapshot } from "../../lib/acp/conversationState";
  import AcpComposer from "./AcpComposer.svelte";
  import ConversationStatus from "./ConversationStatus.svelte";
  import PermissionPrompt from "./PermissionPrompt.svelte";
  import TranscriptView from "./TranscriptView.svelte";

  const NEAR_BOTTOM_PX = 48;

  let {
    controller,
    employeeLabel,
    deferInitialAttach = false
  }: {
    controller: ConversationController;
    employeeLabel: string;
    deferInitialAttach?: boolean;
  } = $props();

  const stableController = untrack(() => controller);
  let snapshot = $state<ConversationSnapshot>(stableController.snapshot());
  let threadElement = $state<HTMLDivElement | null>(null);
  let following = $state(true);
  let jumpVisible = $state(false);
  let initialScrollComplete = false;
  let scrollRenderRequest = 0;
  let lastScrollTop = 0;
  let mounted = $state(false);
  let pendingPermission = $derived(Object.values(snapshot.permissions)[0] ?? null);

  $effect(() => {
    if (mounted && !deferInitialAttach) stableController.attach();
  });

  function distanceFromBottom(element: HTMLDivElement): number {
    return Math.max(0, element.scrollHeight - element.clientHeight - element.scrollTop);
  }

  function updateScrollMode(): void {
    if (!threadElement) return;
    const previousScrollTop = lastScrollTop;
    lastScrollTop = threadElement.scrollTop;
    const nearBottom = distanceFromBottom(threadElement) <= NEAR_BOTTOM_PX;
    if (threadElement.scrollTop < previousScrollTop) following = false;
    else if (threadElement.scrollTop > previousScrollTop && distanceFromBottom(threadElement) <= 1) following = true;
    jumpVisible = !nearBottom;
  }

  function handleThreadWheel(event: WheelEvent): void {
    if (event.deltaY < 0) following = false;
    else if (event.deltaY > 0) {
      window.requestAnimationFrame(() => {
        if (threadElement && distanceFromBottom(threadElement) <= NEAR_BOTTOM_PX) following = true;
      });
    }
  }

  async function scrollThreadToBottom(): Promise<void> {
    following = true;
    await tick();
    if (!threadElement) return;
    threadElement.scrollTop = threadElement.scrollHeight;
    lastScrollTop = threadElement.scrollTop;
    jumpVisible = false;
  }

  $effect.pre(() => {
    snapshot;
    if (!threadElement) return;
    const shouldFollow = !initialScrollComplete || untrack(() => following);
    const request = ++scrollRenderRequest;
    void tick().then(() => {
      if (request !== scrollRenderRequest || !threadElement) return;
      initialScrollComplete = true;
      if (shouldFollow) {
        following = true;
        threadElement.scrollTop = threadElement.scrollHeight;
        lastScrollTop = threadElement.scrollTop;
      }
      jumpVisible = distanceFromBottom(threadElement) > NEAR_BOTTOM_PX;
    });
  });

  onMount(() => {
    const unsubscribe = stableController.subscribe((next) => (snapshot = next));
    mounted = true;
    return () => {
      mounted = false;
      unsubscribe();
      stableController.dispose();
    };
  });
</script>

<div class="chat-panel" data-acp-conversation-pane>
  <div class="chat-head">
    <span class={`chat-dot ${snapshot.connection.state === "ready" ? "chat-dot--on" : "chat-dot--off"}`}></span>
    <span class="chat-lbl">{employeeLabel}</span>
  </div>

  <ConversationStatus
    connection={snapshot.connection}
    activity={snapshot.activity}
    usage={snapshot.session.usage}
    compactions={snapshot.compactions}
    pendingPermission={Boolean(pendingPermission)}
    recoverableError={snapshot.recoverableConnectionError}
    protocolRejections={snapshot.protocolRejections}
  />

  <div class="chat-thread-shell">
    <div
      class="chat-thread"
      data-chat-messages
      bind:this={threadElement}
      onscroll={updateScrollMode}
      onwheel={handleThreadWheel}
    >
      <TranscriptView
        timeline={snapshot.timeline}
        session={snapshot.session}
        compactions={snapshot.compactions}
        receipts={snapshot.receipts}
        protocolRejections={snapshot.protocolRejections}
        unsupportedAgentContent={snapshot.unsupportedAgentContent}
        terminalStates={snapshot.terminalStates}
        onThoughtExpanded={(messageId, partIndex, expanded) => stableController.setThoughtExpanded(messageId, partIndex, expanded)}
        onToolExpanded={(toolCallId, expanded) => stableController.setToolExpanded(toolCallId, expanded)}
      />
    </div>
    {#if jumpVisible}
      <button
        type="button"
        class="chat-jump"
        aria-label="Jump to latest message"
        onclick={() => void scrollThreadToBottom()}
      ><span>Latest</span><span aria-hidden="true">↓</span></button>
    {/if}
  </div>

  {#if pendingPermission}
    <PermissionPrompt
      permission={pendingPermission}
      onSelect={(requestId, optionId) => { stableController.respondToPermission(requestId, optionId); }}
    />
  {/if}

  <AcpComposer
    commands={snapshot.session.availableCommands}
    queue={snapshot.queue}
    receipts={snapshot.receipts}
    latestReceiptClientMessageId={snapshot.latestReceiptClientMessageId}
    activityState={snapshot.activity?.state ?? null}
    supportsSteer={snapshot.connection.supportsSteer}
    {employeeLabel}
    onPrompt={(blocks, choice) => stableController.prompt(blocks, choice)}
    onCancelActive={() => { stableController.cancelActive(); }}
    onCancelQueued={(clientMessageId) => { stableController.cancelQueued(clientMessageId); }}
    onNewConversation={() => { stableController.newConversation(); }}
  />
</div>

<style>
  :global([data-acp-conversation-pane]) { gap: var(--space-2); }
</style>
