<script lang="ts">
  /**
   * ChiefNeutralPane — the entity-generic neutral-envelope pane (Chief + every ticket). A thin
   * view over the framework-free `neutralPane.ts` client that REUSES the legacy chat design:
   * the same message/markdown thread and the real ChatComposer (with disabling turned off and
   * skill-select set to insert, per D-native-turn-concurrency / the owner ruling). Only the two
   * genuinely-new event kinds differ — live agent activity (thinking + tool calls) renders in the
   * thread, and tool approvals get a small inset the legacy pane has no equivalent for.
   *
   * Reactivity boundary: this runs a SEPARATE WebSocket to /api/relay/neutral with its own
   * reconnect + re-attach lifecycle. It registers NO resource, does not import the resource
   * cache, and does not read Panels DB — history renders only from the attach snapshot.
   */
  import { onDestroy, onMount } from "svelte";
  import ChatComposer from "./ChatComposer.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import { uploadChatImage } from "../lib/api";
  import type { CommandCatalog, ChatPendingClarification } from "../lib/types";
  import {
    createNeutralPaneClient,
    type NeutralPaneClient,
    type NeutralSnapshot,
    type NeutralSocket
  } from "../lib/neutralPane";

  let { entityId = "agent_panels_chief_of_staff", label = "Chief of Staff" } = $props<{
    entityId?: string;
    label?: string;
  }>();

  let snapshot = $state<NeutralSnapshot>({
    transcript: [],
    turnActive: false,
    streamingText: "",
    thinkingText: "",
    toolActivity: [],
    pendingQuestion: null,
    pendingApproval: null,
    title: "",
    catalogPayload: null,
    connected: false,
    historyLoaded: false
  });
  let ready = $state(false);
  let composerError = $state("");

  let threadElement = $state<HTMLDivElement | null>(null);
  let following = true;

  function onThreadScroll(): void {
    const el = threadElement;
    if (!el) return;
    following = el.scrollHeight - el.clientHeight - el.scrollTop <= 80;
  }

  // Keep the thread pinned to the newest content while the reader is at the bottom.
  $effect(() => {
    void snapshot.transcript.length;
    void snapshot.streamingText;
    void snapshot.thinkingText;
    void snapshot.toolActivity.length;
    void snapshot.historyLoaded;
    if (!following) return;
    void Promise.resolve().then(() => {
      if (threadElement && following) threadElement.scrollTop = threadElement.scrollHeight;
    });
  });

  let client: NeutralPaneClient | null = null;
  let catalogFetched = false;

  function neutralUrl(): string {
    const scheme = window.location.protocol === "https:" ? "wss://" : "ws://";
    return `${scheme}${window.location.host}/api/relay/neutral`;
  }

  function makeSocket(url: string): NeutralSocket {
    const raw = new WebSocket(url);
    const adapter: NeutralSocket = {
      send: (data) => raw.send(data),
      close: () => raw.close(),
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null
    };
    raw.onopen = () => adapter.onopen?.();
    raw.onmessage = (event: MessageEvent) => adapter.onmessage?.({ data: String(event.data) });
    raw.onclose = () => adapter.onclose?.();
    raw.onerror = () => adapter.onerror?.();
    return adapter;
  }

  onMount(() => {
    client = createNeutralPaneClient({
      url: neutralUrl(),
      employeeEntityId: entityId,
      socketFactory: makeSocket,
      now: () => Date.now(),
      setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
      clearTimeout: (id) => window.clearTimeout(id),
      onState: (next) => {
        snapshot = next;
        if (!ready && next.historyLoaded) ready = true;
      }
    });
    client.attach();
  });

  onDestroy(() => {
    client?.dispose();
  });

  // Fetch the catalog once ready so skills populate the composer's "/" menu.
  $effect(() => {
    if (ready && !catalogFetched) {
      catalogFetched = true;
      client?.listCatalog();
    }
  });

  // --- skills from the native catalog (skills only) ------------------------------------------

  type SkillEntry = { name: string; trigger: string; description?: string };
  const skills = $derived<SkillEntry[]>(extractSkills(snapshot.catalogPayload));

  function extractSkills(payload: unknown): SkillEntry[] {
    if (payload === null || typeof payload !== "object") return [];
    const record = payload as { pairs?: unknown; skill_count?: unknown };
    const pairs = Array.isArray(record.pairs) ? record.pairs : [];
    const skillCount =
      typeof record.skill_count === "number" && record.skill_count > 0
        ? Math.min(record.skill_count, pairs.length)
        : 0;
    if (skillCount === 0) return [];
    const found: SkillEntry[] = [];
    for (const pair of pairs.slice(pairs.length - skillCount)) {
      if (!Array.isArray(pair) || pair.length < 1) continue;
      const command = String(pair[0] ?? "");
      if (!command) continue;
      found.push({
        name: command,
        trigger: command,
        description: pair.length >= 2 ? String(pair[1] ?? "") : undefined
      });
    }
    return found;
  }

  // --- the "/" menu: everything is a slash command, listing only what we handle --------------
  // Session verbs run when picked (they are actions); skills insert their trigger as text.
  // There is no generic command execution — the relay has no path to run arbitrary commands,
  // so only these three actions plus skills appear.
  type CommandItem = { name: string; description: string; run: () => void };

  const COMMAND_ITEMS: CommandItem[] = [
    { name: "/compact", description: "Compress the conversation", run: () => client?.compact() },
    { name: "/new", description: "Start a new conversation", run: () => client?.newConversation() },
    { name: "/interrupt", description: "Stop the current turn", run: () => client?.interrupt() }
  ];

  function matchedCommand(text: string): CommandItem | undefined {
    return COMMAND_ITEMS.find((c) => text === c.name || text.startsWith(`${c.name} `));
  }

  // Adapt our handled actions + native skills into the CommandCatalog shape ChatComposer wants.
  const composerCatalog = $derived<CommandCatalog>({
    categories: [
      { name: "Session", pairs: COMMAND_ITEMS.map((c) => [c.name, c.description] as [string, string]) }
    ],
    skills: skills.map((s) => [s.trigger, s.description ?? ""] as [string, string]),
    canon: {},
    sub: {}
  });

  // A pending agent question maps onto the composer's built-in clarification UI.
  const pendingClarification = $derived<ChatPendingClarification | null>(
    snapshot.pendingQuestion
      ? {
          request_id: snapshot.pendingQuestion.requestId,
          question: snapshot.pendingQuestion.promptText,
          choices: [...snapshot.pendingQuestion.choices]
        }
      : null
  );

  async function uploadFiles(files: File[]): Promise<string[]> {
    const refs: string[] = [];
    for (const file of files) {
      const uploaded = await uploadChatImage(entityId, file);
      refs.push(uploaded.reference);
    }
    return refs;
  }

  // The composer's one submit path, routed to the neutral client. Never disabled — a mid-turn
  // send is a native queue/interrupt, not an error.
  async function composerSubmit(
    text: string,
    _mode: "message" | "command",
    images?: File[]
  ): Promise<boolean> {
    const trimmed = text.trim();
    const question = snapshot.pendingQuestion;
    if (question) {
      if (!trimmed) return false;
      client?.answer(question.requestId, trimmed);
      return true;
    }
    const command = matchedCommand(trimmed);
    if (command && (!images || images.length === 0)) {
      command.run();
      return true;
    }
    let refs: string[] = [];
    if (images && images.length) {
      try {
        refs = await uploadFiles(images);
      } catch {
        composerError = "Image upload failed.";
        return false;
      }
    }
    if (!trimmed && refs.length === 0) return false;
    client?.send(trimmed, refs);
    composerError = "";
    return true;
  }

  function respondApproval(decision: string, applyToAll: boolean): void {
    const approval = snapshot.pendingApproval;
    if (!approval) return;
    client?.respondApproval(approval.requestId, decision, applyToAll);
  }
</script>

<section
  class="chat-panel"
  data-chief-neutral-pane
  data-neutral-ready={ready ? "true" : undefined}
  data-neutral-connected={snapshot.connected ? "true" : undefined}
  aria-label={label}
>
  <div class="chat-head">
    <span class={`chat-dot ${snapshot.connected ? "chat-dot--on" : "chat-dot--off"}`}></span>
    <span class="chat-lbl">{snapshot.connected ? label : `${label} · offline`}</span>
  </div>

  <div class="chat-thread-shell">
    <div
      class="chat-thread"
      data-neutral-transcript
      bind:this={threadElement}
      onscroll={onThreadScroll}
    >
      {#each snapshot.transcript as entry, index (index)}
        {#if entry.role === "human" || entry.role === "user"}
          <div class="chat-u" data-chat-msg="you"><MarkdownBlock text={entry.text} /></div>
        {:else if entry.role === "assistant"}
          <div class="chat-a" data-chat-msg="planner"><MarkdownBlock text={entry.text} /></div>
        {:else if entry.role === "tool"}
          <div class="chat-sys" data-chat-msg="tool"><MarkdownBlock text={entry.text} /></div>
        {:else}
          <div class="chat-sys" data-chat-msg="system"><MarkdownBlock text={entry.text} /></div>
        {/if}
      {/each}

      {#if snapshot.turnActive && snapshot.streamingText}
        <div class="chat-a" data-chat-msg="planner" data-neutral-streaming>
          <MarkdownBlock text={snapshot.streamingText} />
        </div>
      {/if}

      {#if snapshot.thinkingText || snapshot.toolActivity.length}
        <div class="chat-pending-block neutral-activity" data-chat-pending>
          <div class="chat-pending-row">
            <span class="chat-dots" aria-hidden="true"><i></i><i></i><i></i></span>
            <span class="chat-pending-label"
              >{snapshot.thinkingText ? "Thinking" : "Working"}</span
            >
          </div>
          {#if snapshot.thinkingText}
            <div class="neutral-thinking" data-neutral-thinking>{snapshot.thinkingText}</div>
          {/if}
          {#each snapshot.toolActivity as tool (tool.toolId + tool.phase)}
            <div class="neutral-tool" data-neutral-tool>
              <span class="neutral-tool-name">{tool.toolName}</span>
              {#if tool.preview}<span class="neutral-tool-preview">{tool.preview}</span>{/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>

  {#if snapshot.pendingApproval}
    <div class="neutral-inset neutral-approval" data-neutral-approval>
      <p class="neutral-inset-q">{snapshot.pendingApproval.summary}</p>
      <div class="neutral-inset-choices">
        <button
          type="button"
          class="neutral-chip neutral-chip--go"
          data-neutral-approve
          onclick={() => respondApproval("approve", false)}>Approve</button
        >
        <button
          type="button"
          class="neutral-chip"
          data-neutral-approve-all
          onclick={() => respondApproval("approve", true)}>Approve all</button
        >
        <button
          type="button"
          class="neutral-chip neutral-chip--deny"
          data-neutral-deny
          onclick={() => respondApproval("deny", false)}>Deny</button
        >
      </div>
    </div>
  {/if}

  {#if composerError}
    <p class="neutral-error" data-neutral-error>{composerError}</p>
  {/if}

  <ChatComposer
    catalog={composerCatalog}
    disabled={false}
    submitDisabled={false}
    pauseMode={false}
    {pendingClarification}
    placeholder={`Message ${label}…`}
    skillSelectInserts={true}
    onSubmit={composerSubmit}
    onError={(err) => (composerError = err ? "Choose an image file." : "")}
  />
</section>

<style>
  /* Live agent activity (thinking + tool calls), a quiet inset matching the pending block. */
  .neutral-activity {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .neutral-thinking {
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    color: var(--text-faint);
    white-space: pre-wrap;
    padding-left: calc(var(--space-4) + var(--space-1));
  }
  .neutral-tool {
    display: flex;
    gap: var(--space-2);
    align-items: baseline;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding-left: calc(var(--space-4) + var(--space-1));
  }
  .neutral-tool-name { color: var(--text-muted); font-weight: 600; }
  .neutral-tool-preview { color: var(--text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Approval inset — the one genuinely new affordance with no legacy equivalent. */
  .neutral-inset {
    flex: none;
    margin: 0 var(--space-1) var(--space-2);
    padding: var(--space-3);
    border: var(--border-hairline) solid var(--accent-bright);
    border-radius: var(--radius-md);
    background: var(--surface-raised);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .neutral-inset-q { margin: 0; color: var(--text-strong); font-size: var(--type-sm); }
  .neutral-inset-choices { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  .neutral-chip {
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    background: var(--surface-overlay);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding: var(--space-1) var(--space-3);
    cursor: pointer;
    transition: border-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease);
  }
  .neutral-chip:hover { border-color: var(--accent-bright); color: var(--text-strong); }
  .neutral-chip--go { color: var(--text-strong); border-color: var(--accent-bright); }
  .neutral-chip--deny:hover { border-color: var(--accent-error); color: var(--accent-error); }

  .neutral-error { flex: none; margin: 0 var(--space-1) var(--space-2); color: var(--accent-error); font-size: var(--type-xs); }
</style>
