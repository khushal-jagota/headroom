<script lang="ts">
  import { onDestroy, tick, untrack } from "svelte";
  import {
    answerChatClarification,
    pauseChatTurn,
    startChatTurn,
    uploadChatImage
  } from "../lib/api";
  import { resourceCatalogue } from "../lib/resourceCatalogue";
  import type { ChatStateMessage, ChatTurn } from "../lib/types";
  import ChatComposer from "./ChatComposer.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";

  type ChatMessage = {
    renderKey: string;
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

  const commands = resourceCatalogue.chatCommands();
  const chatState = resourceCatalogue.panelsChat(stableEntityId);

  let draft = $state("");
  let error = $state<unknown>(null);
  let activityExpanded = $state(false);
  let activityTurnId = $state<string | null>(null);
  let pausePending = $state(false);
  let threadElement = $state<HTMLDivElement | null>(null);
  let following = $state(true);
  let jumpVisible = $state(false);
  let pollTimer: ReturnType<typeof window.setTimeout> | null = null;
  let initialScrollComplete = false;
  let scrollRenderRequest = 0;
  let lastScrollTop = 0;

  function whoForRole(role: string): ChatMessage["who"] {
    const normalized = role.toLowerCase();
    if (normalized === "user" || normalized === "human") return "you";
    if (normalized === "worker") return "worker";
    if (normalized === "system" || normalized === "tool") return "system";
    return "planner";
  }

  function messageFor(msg: ChatStateMessage): ChatMessage {
    return { renderKey: `message:${msg.id}`, who: whoForRole(msg.role), text: msg.text };
  }

  function activeMessage(turn: ChatTurn): ChatMessage | null {
    if (turn.output_text.trim()) {
      return {
        renderKey: `turn:${turn.id}`,
        who: whoForRole(turn.output_role),
        text: turn.output_text,
        pending: true
      };
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
  let activityEntries = $derived(activeTurn?.activity_entries || []);
  let pendingClarification = $derived(activeTurn?.pending_clarification || null);
  let hasActivityEntries = $derived(activityEntries.length > 0);
  const activityDetailsId = `chat-activity-${stableEntityId}`;

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
    const previousScrollTop = lastScrollTop;
    lastScrollTop = threadElement.scrollTop;
    const nearBottom = distanceFromBottom(threadElement) <= NEAR_BOTTOM_PX;
    if (threadElement.scrollTop < previousScrollTop) {
      following = false;
    } else if (
      threadElement.scrollTop > previousScrollTop &&
      distanceFromBottom(threadElement) <= 1
    ) {
      following = true;
    }
    jumpVisible = !nearBottom;
  }

  function handleThreadWheel(event: WheelEvent): void {
    if (event.deltaY < 0) {
      following = false;
      return;
    }
    if (event.deltaY > 0) {
      window.requestAnimationFrame(() => {
        if (threadElement && distanceFromBottom(threadElement) <= NEAR_BOTTOM_PX) {
          following = true;
        }
      });
    }
  }

  async function scrollThreadToBottom(): Promise<void> {
    following = true;
    await tick();
    if (!threadElement) return;
    threadElement.scrollTop = threadElement.scrollHeight;
    lastScrollTop = threadElement.scrollTop;
    jumpVisible = distanceFromBottom(threadElement) > NEAR_BOTTOM_PX;
  }

  $effect(() => {
    const nextTurnId = activeTurn?.id ?? null;
    if (activityTurnId !== nextTurnId) {
      activityTurnId = nextTurnId;
      activityExpanded = false;
    }
  });

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
    pendingClarification;
    activityEntries;
    activityExpanded;

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
        lastScrollTop = threadElement.scrollTop;
      }
      jumpVisible = distanceFromBottom(threadElement) > NEAR_BOTTOM_PX;
    });
  });

  async function submit(
    text: string,
    mode: "message" | "command",
    images: File[] = []
  ): Promise<boolean> {
    error = null;
    try {
      if (pendingClarification) {
        await answerChatClarification(stableEntityId, {
          request_id: pendingClarification.request_id,
          answer: text
        });
        draft = "";
        await chatState.refresh();
        return true;
      }
      const uploaded = [];
      for (const image of images) {
        uploaded.push(await uploadChatImage(stableEntityId, image));
      }
      await startChatTurn(stableEntityId, {
        text,
        mode,
        ...(uploaded.length
          ? { image_references: uploaded.map((image) => image.reference) }
          : {})
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
    <div
      class="chat-thread"
      data-chat-messages
      bind:this={threadElement}
      onscroll={updateScrollMode}
      onwheel={handleThreadWheel}
    >
      {#if !available}
        <div class="chat-off" data-chat-offline>
          <div>{label} is offline.</div>
          <div class="chat-off-sub">Your draft is saved.</div>
        </div>
      {:else if transcript.length === 0 && !pending && !chatState.loading}
        <div class="chat-empty"><h2 class="chat-empty-h">What do you need?</h2></div>
      {:else}
        {#each transcript as msg (msg.renderKey)}
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
          <div class="chat-pending-block" data-chat-pending data-chat-msg={pendingWho}>
            {#if hasActivityEntries}
              <button
                type="button"
                class="chat-pending-row chat-activity-toggle"
                data-chat-activity-toggle
                aria-expanded={activityExpanded}
                aria-controls={activityDetailsId}
                aria-label={activityExpanded ? "Collapse agent activity" : "Expand agent activity"}
                onclick={() => (activityExpanded = !activityExpanded)}
              >
                <span class="chat-dots" aria-hidden="true"><i></i><i></i><i></i></span>
                <span class="chat-pending-label" data-chat-activity>{pendingLabel}</span>
                <span class="chat-activity-chevron" aria-hidden="true">{activityExpanded ? "⌃" : "⌄"}</span>
              </button>
              {#if activityExpanded}
                <ol
                  id={activityDetailsId}
                  class="chat-activity-details"
                  data-chat-activity-details
                  aria-label="Agent activity details"
                >
                  {#each activityEntries as entry (entry.id)}
                    <li
                      class:chat-activity-entry--running={entry.lifecycle_state === "running"}
                      class="chat-activity-entry"
                      data-chat-activity-entry
                      data-activity-category={entry.category}
                      data-activity-state={entry.lifecycle_state}
                    >
                      <span class="chat-activity-marker" aria-hidden="true"></span>
                      <span class="chat-activity-category">{entry.category}</span>
                      <span class="chat-activity-label">{entry.label}</span>
                      {#if entry.lifecycle_state === "complete"}
                        <span class="chat-activity-state" aria-label="Complete">✓</span>
                      {:else}
                        <span class="chat-activity-state">active</span>
                      {/if}
                    </li>
                  {/each}
                </ol>
              {/if}
            {:else}
              <div class="chat-pending-row">
                <span class="chat-dots" aria-hidden="true"><i></i><i></i><i></i></span>
                <span class="chat-pending-label" data-chat-activity>{pendingLabel}</span>
              </div>
            {/if}
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
      submitDisabled={pending && !pendingClarification}
      pauseMode={pending && !pendingClarification}
      pauseDisabled={!activeTurn?.can_pause}
      {pausePending}
      {pendingClarification}
      initialText={draft}
      placeholder={label === "employee" ? "Message the employee..." : `Message ${label}...`}
      onDraft={(text) => (draft = text)}
      onSubmit={submit}
      onPause={pauseActiveTurn}
      onError={(err) => (error = err)}
    />
  {/if}
</div>
