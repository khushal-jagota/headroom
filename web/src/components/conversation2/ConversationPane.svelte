<script lang="ts">
  /** The pane: who this is, what it has said, and the one box you speak into.
   *
   * The thread follows the bottom while you are at the bottom and stops the moment you
   * scroll away from it, which is the only scrolling behaviour a conversation can have
   * without fighting its reader. Nothing arriving from the server moves the view when you
   * have moved it yourself; the jump button is how you come back.
   */
  import { tick, untrack, type Snippet } from "svelte";
  import ConversationComposer from "./ConversationComposer.svelte";
  import ConversationTranscript from "./ConversationTranscript.svelte";
  import type { RunValues } from "../../lib/conversation2/composer";
  import type { TranscriptRow } from "../../lib/conversation2/transcript";
  import type {
    BackendModel,
    ConversationBackendKey,
    PermissionAskOption,
    PromptDeliveryMode
  } from "../../lib/conversation2/wire";

  const NEAR_BOTTOM_PX = 48;

  let {
    label,
    backendKey = null,
    workspaceFolder = null,
    rows = [],
    running = false,
    ask = null,
    askNote = null,
    current = { model: null, reasoningEffort: null },
    models = [],
    effortOptions = [],
    heldPromptCount = 0,
    fateNote = null,
    errorNote = null,
    connectionTrouble = false,
    composerPlaceholder = "Message the agent...",
    composerDisabled = false,
    emptyState,
    onSend,
    onStop,
    onAnswer,
    onCancelTurn,
    onNewConversation
  }: {
    label: string;
    backendKey?: ConversationBackendKey | null;
    workspaceFolder?: string | null;
    rows?: readonly TranscriptRow[];
    running?: boolean;
    ask?: {
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
    } | null;
    askNote?: string | null;
    current?: RunValues;
    models?: readonly BackendModel[];
    effortOptions?: readonly string[];
    heldPromptCount?: number;
    fateNote?: string | null;
    errorNote?: string | null;
    connectionTrouble?: boolean;
    composerPlaceholder?: string;
    composerDisabled?: boolean;
    emptyState?: Snippet;
    onSend: (text: string, mode: PromptDeliveryMode, picked: RunValues) => Promise<boolean>;
    onStop?: () => void;
    onAnswer?: (optionId: string) => void;
    onCancelTurn?: () => void;
    onNewConversation?: () => void;
  } = $props();

  let threadElement = $state<HTMLDivElement | null>(null);
  let following = $state(true);
  let jumpVisible = $state(false);
  let initialScrollComplete = false;
  let scrollRenderRequest = 0;
  let lastScrollTop = 0;

  let menuOpen = $state(false);
  let confirmArmed = $state(false);
  let menuElement = $state<HTMLDivElement | null>(null);
  let menuButton = $state<HTMLButtonElement | null>(null);

  let headerException = $derived.by(() => {
    if (ask) return { text: "waiting for you", accent: true };
    if (running) return { text: "working", accent: false };
    return null;
  });

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
    onNewConversation?.();
    closeMenu();
  }

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
    rows;
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
</script>

<svelte:window onpointerdown={onWindowPointerDown} onkeydown={onWindowKeydown} />

<div class="chat-panel" data-conversation2-pane>
  <div class="chat-head">
    {#if connectionTrouble}
      <span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>
    {/if}
    <span class="chat-lbl">{label}</span>
    {#if headerException}
      <span class={`chat-state ${headerException.accent ? "chat-state--attn" : ""}`}>
        {headerException.text}
      </span>
    {/if}
    <div class="chat-head-right">
      {#if workspaceFolder}
        <span class="chat-usage" data-conversation2-workspace>{workspaceFolder}</span>
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
              <button
                type="button"
                class="chat-overflow-item chat-overflow-item--confirm"
                role="menuitem"
                data-conversation2-new-confirm
                onclick={confirmNewConversation}
              >Confirm — this kills the old one</button>
            {:else}
              <button
                type="button"
                class="chat-overflow-item"
                role="menuitem"
                data-conversation2-new-arm
                onclick={() => (confirmArmed = true)}
              >New conversation</button>
            {/if}
          </div>
        {/if}
      </div>
    </div>
  </div>

  <div class="chat-thread-shell">
    <div
      class="chat-thread"
      data-conversation2-thread
      bind:this={threadElement}
      onscroll={updateScrollMode}
      onwheel={handleThreadWheel}
    >
      {#if emptyState && rows.length === 0}
        {@render emptyState()}
      {/if}
      <ConversationTranscript {rows} {models} />
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

  <ConversationComposer
    {backendKey}
    {running}
    {ask}
    {askNote}
    {current}
    {models}
    {effortOptions}
    {heldPromptCount}
    {fateNote}
    {errorNote}
    placeholder={composerPlaceholder}
    disabled={composerDisabled}
    {onSend}
    {onStop}
    {onAnswer}
    {onCancelTurn}
  />
</div>

<style>
  :global([data-conversation2-pane]) { gap: var(--space-2); }
</style>
