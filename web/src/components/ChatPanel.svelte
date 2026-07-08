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

  let { entityId, available }: { entityId: string; available: boolean } = $props();
  const stableEntityId = untrack(() => entityId);

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
    commands.dispose();
    history.dispose();
  });
</script>

<div class="chat-panel" data-chat-panel>
  <div class="chat-head">
    <span class={`chat-dot ${available ? "chat-dot--on" : "chat-dot--off"}`}></span>
    <span class="chat-lbl">{available ? "employee" : "employee · offline"}</span>
  </div>

  <div class="chat-thread" data-chat-messages>
    {#if !available}
      <div class="chat-off" data-chat-offline>
        <div>The employee is offline.</div>
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
        {:else}
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
      onDraft={(text) => (draft = text)}
      onSubmit={submit}
    />
  {/if}
</div>
