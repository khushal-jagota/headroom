<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import type { ConversationController } from "../../lib/acp/conversationController";
  import type { ConversationSnapshot } from "../../lib/acp/conversationState";
  import AcpComposer from "./AcpComposer.svelte";
  import ConversationStatus from "./ConversationStatus.svelte";
  import PermissionPrompt from "./PermissionPrompt.svelte";
  import TaskProgressStrip from "./TaskProgressStrip.svelte";
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

  let connectionTrouble = $derived(
    ["closed", "error"].includes(snapshot.connection.state)
    || Boolean(snapshot.recoverableConnectionError)
  );
  let compacting = $derived(
    Object.values(snapshot.compactions).some((item) => item.payload.state === "compacting")
  );
  let headerException = $derived.by(() => {
    if (pendingPermission) return { text: "waiting for you", accent: true };
    if (compacting) return { text: "compacting", accent: false };
    return null;
  });
  let usageLabel = $derived.by(() => {
    const usage = snapshot.session.usage;
    if (!usage) return null;
    const budget = `${formatTokens(usage.used)} / ${formatTokens(usage.size)}`;
    if (!usage.cost) return budget;
    const cost = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: usage.cost.currency
    }).format(usage.cost.amount);
    return `${budget} · ${cost}`;
  });

  function formatTokens(count: number): string {
    if (count < 1000) return String(count);
    return `${(count / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  }

  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);

  function closeMenu(): void {
    menuOpen = false;
    confirmArmed = false;
  }

  function onWindowPointerDown(event: PointerEvent): void {
    if (menuOpen && menuElement && !menuElement.contains(event.target as Node)) closeMenu();
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && menuOpen) {
      closeMenu();
      menuButton?.focus();
    }
  }

  function confirmNewConversation(): void {
    stableController.newConversation();
    closeMenu();
  }

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

<svelte:window onpointerdown={onWindowPointerDown} onkeydown={onWindowKeydown} />

<div class="chat-panel" data-acp-conversation-pane>
  <div class="chat-head">
    {#if connectionTrouble}
      <span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>
    {/if}
    <span class="chat-lbl">{employeeLabel}</span>
    {#if headerException}
      <span class={`chat-state ${headerException.accent ? "chat-state--attn" : ""}`}>{headerException.text}</span>
    {/if}
    <div class="chat-head-right">
      {#if usageLabel}
        <span class="chat-usage">{usageLabel}</span>
      {/if}
      <div class="chat-overflow" bind:this={menuElement}>
        <button
          type="button"
          class="chat-overflow-btn"
          bind:this={menuButton}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Conversation options"
          onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
        >⋯</button>
        {#if menuOpen}
          <div class="chat-overflow-menu" role="menu">
            {#if confirmArmed}
              <button type="button" class="chat-overflow-item chat-overflow-item--confirm" role="menuitem" onclick={confirmNewConversation}>Confirm</button>
            {:else}
              <button type="button" class="chat-overflow-item" role="menuitem" onclick={() => (confirmArmed = true)}>New conversation</button>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </div>

  <ConversationStatus
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
        activity={snapshot.activity}
        compactions={snapshot.compactions}
        receipts={snapshot.receipts}
        protocolRejections={snapshot.protocolRejections}
        unsupportedAgentContent={snapshot.unsupportedAgentContent}
        terminalStates={snapshot.terminalStates}
        programmaticPrompts={snapshot.programmaticPrompts}
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

  <TaskProgressStrip plan={snapshot.session.plan} activity={snapshot.activity} />

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
  />
</div>

<style>
  :global([data-acp-conversation-pane]) { gap: var(--space-2); }
</style>
