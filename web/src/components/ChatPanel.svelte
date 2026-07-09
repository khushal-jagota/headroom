<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { fetchJson, startChatTurn } from "../lib/api";
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
  let pollTimer: ReturnType<typeof window.setTimeout> | null = null;

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
  let pendingLabel = $derived(
    activeTurn?.activity_label || (activeTurn?.phase === "doing" ? "Working" : "Thinking")
  );

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

  $effect(() => {
    if (activeTurn && !chatState.loading) {
      schedulePoll();
      return;
    }
    if (!activeTurn) clearPoll();
  });

  async function submit(text: string, mode: "message" | "command"): Promise<void> {
    error = null;
    try {
      await startChatTurn(stableEntityId, { text, mode });
      await chatState.refresh();
    } catch (err) {
      error = err;
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

  <div class="chat-thread" data-chat-messages>
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
          <div class="chat-u" data-chat-msg="you">{msg.text}</div>
        {:else if msg.who === "worker"}
          <div class="chat-sys" data-chat-msg="worker">{msg.text}</div>
        {:else if msg.who === "system"}
          <div class="chat-sys" data-chat-msg="system">{msg.text}</div>
        {:else if msg.text.trim()}
          <div class="chat-a" data-chat-msg="planner"><MarkdownBlock text={msg.text} /></div>
        {/if}
      {/each}
      {#if pending}
        <div class="chat-pending-row" data-chat-pending data-chat-msg={pendingWho}>
          <span class="chat-dots" aria-hidden="true"><i></i><i></i><i></i></span>
          <span class="chat-pending-label">{pendingLabel}</span>
        </div>
      {/if}
    {/if}
  </div>

  {#if error}
    <ErrorLine {error} />
  {/if}

  {#if available}
    <ChatComposer
      catalog={commands.data}
      disabled={pending}
      initialText={draft}
      placeholder={label === "employee" ? "Message the employee..." : `Message ${label}...`}
      onDraft={(text) => (draft = text)}
      onSubmit={submit}
    />
  {/if}
</div>
