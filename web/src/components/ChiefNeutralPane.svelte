<script lang="ts">
  /**
   * ChiefNeutralPane — the Chief-only neutral-envelope pane (S2b plan §4.4). A thin view over
   * the framework-free `neutralPane.ts` client, with its OWN dedicated composer (NOT
   * ChatComposer, which auto-sends skills/commands and carries a disabling busy state — both
   * contract violations). NEVER disabled, no busy state; a mid-turn send is a native queue,
   * not an error (D-native-turn-concurrency).
   *
   * Reactivity boundary: this runs a SEPARATE WebSocket to /api/relay/neutral with its own
   * reconnect + re-attach lifecycle. It registers NO resource, does not import the resource
   * cache, and does not read Panels DB — history renders only from the attach snapshot. So the
   * resource-catalogue completeness test stays green.
   */
  import { onDestroy, onMount, tick } from "svelte";
  import { uploadChatImage } from "../lib/api";
  import {
    createPendingChatImages,
    pendingChatImageFiles,
    removePendingChatImage,
    revokePendingChatImages
  } from "../lib/chatImages";
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
  let draft = $state("");
  let answerDraft = $state("");
  let pickerOpen = $state(false);
  let pendingImages = $state<{ id: number; file: File; url: string }[]>([]);
  let nextImageId = 1;
  let imageInput = $state<HTMLInputElement | null>(null);
  let uploadError = $state("");

  let client: NeutralPaneClient | null = null;

  function neutralUrl(): string {
    const scheme = window.location.protocol === "https:" ? "wss://" : "ws://";
    return `${scheme}${window.location.host}/api/relay/neutral`;
  }

  /** Adapt the DOM WebSocket (its handlers carry an Event arg the client ignores) to the
   * client's minimal NeutralSocket shape. */
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
        // The readiness barrier gates on the FIRST history snapshot rendering — a socket open
        // alone is not ready (Playwright must never race the pre-history mount).
        if (!ready && next.historyLoaded) ready = true;
      }
    });
    client.attach();
  });

  onDestroy(() => {
    client?.dispose();
    revokePendingChatImages(pendingImages, URL.revokeObjectURL);
    pendingImages = [];
  });

  // --- skills-only picker (from the catalog_result payload, skills only) ---------------------

  type SkillEntry = { name: string; trigger: string; description?: string };

  const skills = $derived<SkillEntry[]>(extractSkills(snapshot.catalogPayload));

  /** Parse skills from the NATIVE `commands.catalog` payload (delivered verbatim by S2a). The
   * native shape (shared_gateway._build_catalog) is top-level `pairs` (list of
   * [command, description]) + `skill_count`; SKILLS are the LAST `skill_count` entries of
   * `pairs`. The command string IS the trigger text to insert. Skills-only — never runnable
   * commands, never /model. */
  function extractSkills(payload: unknown): SkillEntry[] {
    if (payload === null || typeof payload !== "object") return [];
    const record = payload as { pairs?: unknown; skill_count?: unknown };
    const pairs = Array.isArray(record.pairs) ? record.pairs : [];
    const skillCount =
      typeof record.skill_count === "number" && record.skill_count > 0
        ? Math.min(record.skill_count, pairs.length)
        : 0;
    if (skillCount === 0) return [];
    const skillPairs = pairs.slice(pairs.length - skillCount);
    const found: SkillEntry[] = [];
    for (const pair of skillPairs) {
      if (!Array.isArray(pair) || pair.length < 1) continue;
      const command = String(pair[0] ?? "");
      if (!command) continue;
      found.push({
        name: command,
        trigger: command, // the command string is the trigger text
        description: pair.length >= 2 ? String(pair[1] ?? "") : undefined
      });
    }
    return found;
  }

  function togglePicker(): void {
    pickerOpen = !pickerOpen;
    if (pickerOpen) client?.listCatalog(); // fetch the catalog on picker OPEN (F10)
  }

  function insertSkill(skill: SkillEntry): void {
    // INSERT the trigger text into the draft — never auto-send.
    draft = draft.length ? `${draft} ${skill.trigger}` : skill.trigger;
    pickerOpen = false;
  }

  // --- images (reuse the existing chatImages helper + uploadChatImage) -----------------------

  function intakeFiles(files: FileList | null | undefined): void {
    if (!files) return;
    const result = createPendingChatImages(files, nextImageId, URL.createObjectURL);
    pendingImages = [...pendingImages, ...result.accepted];
    nextImageId = result.nextId;
    if (result.rejected.length) uploadError = "Choose an image file.";
  }

  function imageChanged(): void {
    intakeFiles(imageInput?.files);
    if (imageInput) imageInput.value = "";
  }

  function removeImage(id: number): void {
    pendingImages = removePendingChatImage(pendingImages, id, URL.revokeObjectURL);
  }

  async function uploadPendingImages(): Promise<string[]> {
    const refs: string[] = [];
    for (const file of pendingChatImageFiles(pendingImages)) {
      const uploaded = await uploadChatImage(entityId, file);
      refs.push(uploaded.reference);
    }
    return refs;
  }

  // --- composer actions (NEVER disabled — a mid-turn send is a native queue) ------------------

  async function submit(): Promise<void> {
    const text = draft.trim();
    if (!text && pendingImages.length === 0) return;
    let refs: string[] = [];
    if (pendingImages.length) {
      try {
        refs = await uploadPendingImages();
      } catch {
        uploadError = "Image upload failed.";
        return;
      }
    }
    client?.send(text, refs);
    draft = "";
    revokePendingChatImages(pendingImages, URL.revokeObjectURL);
    pendingImages = [];
    uploadError = "";
    await tick();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  function interrupt(): void {
    client?.interrupt();
  }

  function compact(): void {
    client?.compact();
  }

  function newConversation(): void {
    client?.newConversation();
  }

  function answerQuestion(choice?: string): void {
    const question = snapshot.pendingQuestion;
    if (!question) return;
    const answer = (choice ?? answerDraft).trim();
    if (!answer) return;
    client?.answer(question.requestId, answer);
    answerDraft = "";
  }

  function respondApproval(decision: string, applyToAll: boolean): void {
    const approval = snapshot.pendingApproval;
    if (!approval) return;
    client?.respondApproval(approval.requestId, decision, applyToAll);
  }
</script>

<section
  class="chief-neutral-pane"
  data-chief-neutral-pane
  data-neutral-ready={ready ? "true" : undefined}
  data-neutral-connected={snapshot.connected ? "true" : undefined}
  aria-label={label}
>
  <div class="chief-neutral-transcript" data-neutral-transcript>
    {#each snapshot.transcript as entry, index (index)}
      {#if entry.role === "human" || entry.role === "user"}
        <div class="chat-u" data-chat-msg="you">{entry.text}</div>
      {:else if entry.role === "assistant"}
        <div class="chat-a" data-chat-msg="planner">{entry.text}</div>
      {:else if entry.role === "tool"}
        <div class="chat-sys" data-chat-msg="tool">{entry.text}</div>
      {:else}
        <div class="chat-sys" data-chat-msg="system">{entry.text}</div>
      {/if}
    {/each}

    {#if snapshot.thinkingText}
      <div class="chief-neutral-thinking" data-neutral-thinking>{snapshot.thinkingText}</div>
    {/if}

    {#if snapshot.turnActive && snapshot.streamingText}
      <div class="chat-a" data-chat-msg="planner" data-neutral-streaming>
        {snapshot.streamingText}
      </div>
    {/if}

    {#each snapshot.toolActivity as tool (tool.toolId + tool.phase)}
      <div class="chief-neutral-tool" data-neutral-tool>{tool.toolName}: {tool.preview}</div>
    {/each}
  </div>

  {#if snapshot.pendingQuestion}
    <div class="chief-neutral-clarify" data-neutral-clarify>
      <p data-neutral-question>{snapshot.pendingQuestion.promptText}</p>
      {#each snapshot.pendingQuestion.choices as choice (choice)}
        <button
          type="button"
          data-neutral-choice
          onclick={() => answerQuestion(choice)}
        >{choice}</button>
      {/each}
      <input
        type="text"
        data-neutral-answer-input
        bind:value={answerDraft}
        placeholder="Answer..."
      />
      <button type="button" data-neutral-answer-send onclick={() => answerQuestion()}>Answer</button>
    </div>
  {/if}

  {#if snapshot.pendingApproval}
    <div class="chief-neutral-approval" data-neutral-approval>
      <p>{snapshot.pendingApproval.summary}</p>
      <button type="button" data-neutral-approve onclick={() => respondApproval("approve", false)}
        >Approve</button
      >
      <button type="button" data-neutral-approve-all onclick={() => respondApproval("approve", true)}
        >Approve all</button
      >
      <button type="button" data-neutral-deny onclick={() => respondApproval("deny", false)}
        >Deny</button
      >
    </div>
  {/if}

  <div class="chief-neutral-composer" data-neutral-composer>
    {#if pickerOpen}
      <div class="chief-neutral-picker" data-neutral-picker>
        {#each skills as skill (skill.name)}
          <button
            type="button"
            data-neutral-skill
            data-neutral-skill-name={skill.name}
            onclick={() => insertSkill(skill)}
          >{skill.name}</button>
        {/each}
        {#if skills.length === 0}
          <span data-neutral-picker-empty>No skills</span>
        {/if}
      </div>
    {/if}

    {#if pendingImages.length}
      <div class="chief-neutral-images" data-neutral-images>
        {#each pendingImages as image (image.id)}
          <span class="chief-neutral-image" data-neutral-image data-neutral-image-name={image.file.name}>
            <img src={image.url} alt="" />
            <button type="button" data-neutral-image-remove onclick={() => removeImage(image.id)}
              >×</button
            >
          </span>
        {/each}
      </div>
    {/if}

    {#if uploadError}
      <p class="chief-neutral-error" data-neutral-error>{uploadError}</p>
    {/if}

    <textarea
      data-chat-input
      bind:value={draft}
      onkeydown={onKeydown}
      placeholder={`Message ${label}...`}
      rows="2"
    ></textarea>

    <div class="chief-neutral-actions">
      <button type="button" data-neutral-picker-toggle onclick={togglePicker}>Skills</button>
      <input
        type="file"
        accept="image/*"
        bind:this={imageInput}
        onchange={imageChanged}
        data-neutral-image-input
        hidden
      />
      <button type="button" data-neutral-image-add onclick={() => imageInput?.click()}>Image</button>
      <button type="button" data-neutral-compact onclick={compact}>Compact</button>
      <button type="button" data-neutral-new-conversation onclick={newConversation}>New</button>
      <button type="button" data-neutral-interrupt onclick={interrupt}>Interrupt</button>
      <button type="button" data-chat-send onclick={() => void submit()}>Send</button>
    </div>
  </div>
</section>

<style>
  /* Fill the flex parent and establish the internal scroll region — mirrors the
     legacy .chat-panel/.chat-thread structure in assets/app.css so the transcript
     scrolls instead of expanding its container (which the desk clips at overflow
     hidden). Entity-generic: applies to the Chief and every ticket pane. */
  .chief-neutral-pane {
    flex: 1;
    min-height: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .chief-neutral-transcript {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    padding: var(--space-3) var(--space-1) var(--space-4);
  }

  .chief-neutral-clarify,
  .chief-neutral-approval,
  .chief-neutral-composer {
    flex: none;
  }
</style>
