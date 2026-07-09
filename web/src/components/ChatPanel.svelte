<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { fetchJson, streamChat } from "../lib/api";
  import { resource } from "../lib/resources";
  import type { ChatHistoryResponse, CommandCatalog } from "../lib/types";
  import ChatComposer from "./ChatComposer.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";

  type ChatMessage = {
    who: "you" | "planner" | "system";
    text: string;
  };

  let {
    entityId,
    available,
    ticketStatus = "",
    label = "employee"
  }: {
    entityId: string;
    available: boolean;
    ticketStatus?: string;
    label?: string;
  } = $props();
  const stableEntityId = untrack(() => entityId);
  const isTicketChat = stableEntityId.startsWith("t_");

  const commands = resource<CommandCatalog>("chat-commands", (signal) =>
    fetchJson("/api/chat/commands", { signal })
  );
  const history = resource<ChatHistoryResponse>(`chat:${stableEntityId}`, (signal) =>
    fetchJson(`/api/chat/${stableEntityId}/history`, { signal })
  );
  if (history.data !== undefined && !history.stale) {
    void history.refresh().catch(() => undefined);
  }

  let transcript = $state<ChatMessage[]>([]);
  let draft = $state("");
  let pending = $state(false);
  let error = $state<unknown>(null);
	  let controller: AbortController | null = null;
	  let workerRefreshTimer: ReturnType<typeof window.setTimeout> | null = null;
	  let settledRefreshKey = "";
	  let settledBaselineSignature = "";
	  let settledRefreshesRemaining = 0;
	  let historySignature = $state("");

  function whoForRole(role: string): ChatMessage["who"] {
    const normalized = role.toLowerCase();
    if (normalized === "user" || normalized === "human") return "you";
    if (normalized === "system" || normalized === "tool") return "system";
    return "planner";
  }

  function signatureFor(historyData: ChatHistoryResponse | undefined): string {
    return JSON.stringify(
      (historyData?.messages || []).map((msg) => [msg.role, msg.text, msg.created_at])
    );
  }

  $effect(() => {
    if (pending) return;
    const signature = signatureFor(history.data);
    if (signature === historySignature) return;
    historySignature = signature;
    transcript = (history.data?.messages || []).map((msg) => ({
      who: whoForRole(msg.role),
      text: msg.text
    }));
  });

  function clearWorkerRefresh(): void {
    if (workerRefreshTimer === null) return;
    window.clearTimeout(workerRefreshTimer);
    workerRefreshTimer = null;
  }

  function scheduleWorkerHistoryRefresh(delayMs: number, expectedSignature?: string): void {
    if (workerRefreshTimer !== null) return;
    workerRefreshTimer = window.setTimeout(() => {
      workerRefreshTimer = null;
      const hasRenderedTranscript =
        transcript.length > 0 || document.querySelector("[data-chat-msg]") !== null;
      if (
        expectedSignature !== undefined &&
        (hasRenderedTranscript || signatureFor(history.data) !== expectedSignature)
      ) {
        resetSettledRetry();
        return;
      }
      void history.refresh().catch(() => undefined);
    }, delayMs);
  }

  function resetSettledRetry(): void {
    settledRefreshKey = "";
    settledBaselineSignature = "";
    settledRefreshesRemaining = 0;
  }

  $effect(() => {
    if (!available || !isTicketChat) {
      resetSettledRetry();
      clearWorkerRefresh();
      return;
    }
    const status = ticketStatus || "";
    if (status === "agent_running_step") {
      resetSettledRetry();
      if (!history.loading) scheduleWorkerHistoryRefresh(2000);
      return;
    }
    if (status === "awaiting_approval" || status === "errored") {
      if (history.loading || history.data === undefined) return;
      const signature = signatureFor(history.data);
      const shouldRetry = (history.data.messages || []).length === 0;
      if (!shouldRetry) {
        resetSettledRetry();
        clearWorkerRefresh();
        return;
      }
      const key = `${stableEntityId}:${status}`;
      if (key !== settledRefreshKey) {
        settledRefreshKey = key;
        settledBaselineSignature = signature;
        settledRefreshesRemaining = 6;
      } else if (signature !== settledBaselineSignature) {
        resetSettledRetry();
        clearWorkerRefresh();
        return;
      }
      if (!history.loading && settledRefreshesRemaining > 0) {
        settledRefreshesRemaining -= 1;
        scheduleWorkerHistoryRefresh(1000, settledBaselineSignature);
      }
      return;
    }
    resetSettledRetry();
    clearWorkerRefresh();
  });

  function eventData<T extends Record<string, unknown>>(data: unknown): T {
    return data && typeof data === "object" ? (data as T) : ({} as T);
  }

  async function submit(text: string, mode: "message" | "command"): Promise<void> {
    transcript = [...transcript, { who: "you", text }, { who: "planner", text: "" }];
    const replyIndex = transcript.length - 1;
    pending = true;
    error = null;
    controller?.abort();
    controller = new AbortController();
    try {
      await streamChat(
        stableEntityId,
        { text, mode },
        {
          signal: controller.signal,
          onEvent(event, data) {
            if (event === "token") {
              const payload = eventData<{ text?: unknown }>(data);
              transcript[replyIndex].text += String(payload.text || "");
            }
            if (event === "message_done") {
              const payload = eventData<{
                reply_text?: unknown;
                kind?: unknown;
              }>(data);
              transcript[replyIndex].text = String(payload.reply_text || "");
              transcript[replyIndex].who = payload.kind === "system" ? "system" : "planner";
              pending = false;
              void history.refresh().catch(() => undefined);
            }
            if (event === "error") {
              error = data;
              pending = false;
            }
          }
        }
      );
    } catch (err) {
      error = err;
      pending = false;
    }
  }

  onDestroy(() => {
    controller?.abort();
    clearWorkerRefresh();
    commands.dispose();
    history.dispose();
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
    {:else if transcript.length === 0 && !pending && !history.loading}
      <div class="chat-empty"><h2 class="chat-empty-h">What do you need?</h2></div>
    {:else}
      {#each transcript as msg}
        {#if msg.who === "you"}
          <div class="chat-u" data-chat-msg="you">{msg.text}</div>
        {:else if msg.who === "system"}
          <div class="chat-sys" data-chat-msg="system">{msg.text}</div>
        {:else if msg.text.trim()}
          <div class="chat-a" data-chat-msg="planner"><MarkdownBlock text={msg.text} /></div>
        {/if}
      {/each}
      {#if pending}
        <div class="chat-dots" data-chat-pending><i></i><i></i><i></i></div>
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
