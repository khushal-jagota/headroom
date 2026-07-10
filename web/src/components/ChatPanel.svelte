<script lang="ts">
  import { onDestroy, tick, untrack } from "svelte";
  import { fetchJson, pauseChatTurn, startChatTurn, uploadChatImage } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { ChatStateMessage, ChatStateResponse, ChatTurn, CommandCatalog } from "../lib/types";
  import ChatComposer from "./ChatComposer.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";

  type ChatMessage = {
    who: "you" | "planner" | "system" | "worker";
    text: string;
    pending?: boolean;
  };

  const NEAR_BOTTOM_PX = 48;

  let {
    entityId,
    available,
    label = "employee"
  }: {
    entityId: string;
    available: boolean;
    label?: string;
  } = $props();
  const stableEntityId = untrack(() => entityId);

  const commands = resource<CommandCatalog>("chat-commands", (signal) =>
    fetchJson("/api/chat/commands", { signal })
  );
  const chatState = resource<ChatStateResponse>(`chat:${stableEntityId}`, (signal) =>
    fetchJson(`/api/chat/${stableEntityId}/state`, { signal })
  );
  if (chatState.data !== undefined && !chatState.stale) {
    void chatState.refresh().catch(() => undefined);
  }

  let draft = $state("");
  let error = $state<unknown>(null);
  let pausePending = $state(false);
  let threadElement = $state<HTMLDivElement | null>(null);
  let following = $state(true);
  let jumpVisible = $state(false);
  let pollTimer: ReturnType<typeof window.setTimeout> | null = null;
  let initialScrollComplete = false;
  let scrollRenderRequest = 0;

  function whoForRole(role: string): ChatMessage["who"] {
    const normalized = role.toLowerCase();
    if (normalized === "user" || normalized === "human") return "you";
    if (normalized === "worker") return "worker";
    if (normalized === "system" || normalized === "tool") return "system";
    return "planner";
  }

  function messageFor(msg: ChatStateMessage): ChatMessage {
    return { who: whoForRole(msg.role), text: msg.text };
  }

  function activeMessage(turn: ChatTurn): ChatMessage | null {
    if (turn.output_text.trim()) {
      return { who: whoForRole(turn.output_role), text: turn.output_text, pending: true };
    }
    return null;
  }

  let transcript = $derived.by<ChatMessage[]>(() => {
    const messages = (chatState.data?.messages || []).map(messageFor);
    const turn = chatState.data?.active_turn || null;
    const live = turn ? activeMessage(turn) : null;
    return live ? [...messages, live] : messages;
  });

  let activeTurn = $derived(chatState.data?.active_turn || null);
  let pending = $derived(Boolean(activeTurn));
  let pendingWho = $derived(activeTurn?.origin === "worker" ? "worker" : "planner");

  function pendingLabelFor(turn: ChatTurn | null): string {
    const label = turn?.activity_label?.trim();
    if (label) return label;
    if (turn?.phase === "queued") return "Queued";
    if (turn?.phase === "doing") return "Working";
    if (turn?.phase === "responding") return "Responding";
    return "Thinking";
  }

  let pendingLabel = $derived(pendingLabelFor(activeTurn));

  function clearPoll(): void {
    if (pollTimer === null) return;
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }

  function schedulePoll(): void {
    if (pollTimer !== null || !activeTurn) return;
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      void chatState.refresh().catch(() => undefined);
    }, 500);
  }

  function distanceFromBottom(element: HTMLDivElement): number {
    return Math.max(0, element.scrollHeight - element.clientHeight - element.scrollTop);
  }

  function updateScrollMode(): void {
    if (!threadElement) return;
    const nearBottom = distanceFromBottom(threadElement) <= NEAR_BOTTOM_PX;
    following = nearBottom;
    jumpVisible = !nearBottom;
  }

  async function scrollThreadToBottom(): Promise<void> {
    following = true;
    await tick();
    if (!threadElement) return;
    threadElement.scrollTop = threadElement.scrollHeight;
    jumpVisible = distanceFromBottom(threadElement) > NEAR_BOTTOM_PX;
  }

  $effect(() => {
    if (activeTurn && !chatState.loading) {
      schedulePoll();
      return;
    }
    if (!activeTurn) clearPoll();
  });

  $effect.pre(() => {
    transcript;
    pending;
    pendingLabel;

    if (chatState.loading || chatState.data === undefined || !threadElement) {
      return;
    }

    const shouldFollow = !initialScrollComplete || untrack(() => following);
    const request = ++scrollRenderRequest;
    void tick().then(() => {
      if (request !== scrollRenderRequest || !threadElement) return;
      initialScrollComplete = true;
      if (shouldFollow) {
        following = true;
        threadElement.scrollTop = threadElement.scrollHeight;
      }
      jumpVisible = distanceFromBottom(threadElement) > NEAR_BOTTOM_PX;
    });
  });

  async function submit(
    text: string,
    mode: "message" | "command",
    image?: File
  ): Promise<boolean> {
    error = null;
    try {
      const uploaded = image ? await uploadChatImage(stableEntityId, image) : null;
      await startChatTurn(stableEntityId, {
        text,
        mode,
        ...(uploaded ? { image_reference: uploaded.reference } : {})
      });
    } catch (err) {
      error = err;
      return false;
    }
    try {
      await chatState.refresh();
    } catch (err) {
      error = err;
    }
    return true;
  }

  async function pauseActiveTurn(): Promise<void> {
    if (!activeTurn || pausePending) return;
    error = null;
    pausePending = true;
    try {
      await pauseChatTurn(stableEntityId);
      await chatState.refresh();
    } catch (err) {
      error = err;
    } finally {
      pausePending = false;
    }
  }

  onDestroy(() => {
    clearPoll();
    commands.dispose();
    chatState.dispose();
  });
</script>

<div class="chat-panel" data-chat-panel>
  <div class="chat-head">
    <span class={`chat-dot ${available ? "chat-dot--on" : "chat-dot--off"}`}></span>
    <span class="chat-lbl">{available ? label : `${label} · offline`}</span>
  </div>

  <div class="chat-thread-shell">
    <div class="chat-thread" data-chat-messages bind:this={threadElement} onscroll={updateScrollMode}>
      {#if !available}
        <div class="chat-off" data-chat-offline>
          <div>{label} is offline.</div>
          <div class="chat-off-sub">Your draft is saved.</div>
        </div>
      {:else if transcript.length === 0 && !pending && !chatState.loading}
        <div class="chat-empty"><h2 class="chat-empty-h">What do you need?</h2></div>
      {:else}
        {#each transcript as msg}
          {#if msg.who === "you"}
            <div class="chat-u" data-chat-msg="you"><MarkdownBlock text={msg.text} /></div>
          {:else if msg.who === "worker"}
            <div class="chat-sys" data-chat-msg="worker"><MarkdownBlock text={msg.text} /></div>
          {:else if msg.who === "system"}
            <div class="chat-sys" data-chat-msg="system"><MarkdownBlock text={msg.text} /></div>
          {:else if msg.text.trim()}
            <div class="chat-a" data-chat-msg="planner"><MarkdownBlock text={msg.text} /></div>
          {/if}
        {/each}
        {#if pending}
          <div class="chat-pending-row" data-chat-pending data-chat-msg={pendingWho}>
            <span class="chat-dots" aria-hidden="true"><i></i><i></i><i></i></span>
            <span class="chat-pending-label" data-chat-activity>{pendingLabel}</span>
          </div>
        {/if}
      {/if}
    </div>

    {#if jumpVisible}
      <button
        type="button"
        class="chat-jump"
        data-chat-jump
        aria-label="Jump to latest message"
        onclick={() => void scrollThreadToBottom()}
      >
        <span>Latest</span><span aria-hidden="true">↓</span>
      </button>
    {/if}
  </div>

  {#if error}
    <ErrorLine {error} />
  {/if}

  {#if available}
    <ChatComposer
      catalog={commands.data}
      submitDisabled={pending}
      pauseMode={pending}
      pauseDisabled={!activeTurn?.session_key}
      {pausePending}
      initialText={draft}
      placeholder={label === "employee" ? "Message the employee..." : `Message ${label}...`}
      onDraft={(text) => (draft = text)}
      onSubmit={submit}
      onPause={pauseActiveTurn}
      onError={(err) => (error = err)}
    />
  {/if}
</div>
