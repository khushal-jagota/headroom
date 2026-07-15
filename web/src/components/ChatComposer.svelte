<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    clearSentPendingChatImages,
    createPendingChatImages,
    pendingChatImageFiles,
    removePendingChatImage,
    revokePendingChatImages
  } from "../lib/chatImages.js";
  import type { ChatPendingClarification, CommandCatalog } from "../lib/types";

  type MenuItem = { name: string; description: string; skill: boolean };
  type PendingImage = { id: number; file: File; url: string };

  let {
    catalog,
    disabled = false,
    submitDisabled = false,
    pauseMode = false,
    pauseDisabled = false,
    pausePending = false,
    pendingClarification = null,
    initialText = "",
    placeholder = "Message the employee...",
    onDraft,
    onSubmit,
    onPause,
    onError
  }: {
    catalog?: CommandCatalog;
    disabled?: boolean;
    submitDisabled?: boolean;
    pauseMode?: boolean;
    pauseDisabled?: boolean;
    pausePending?: boolean;
    pendingClarification?: ChatPendingClarification | null;
    initialText?: string;
    placeholder?: string;
    onDraft?: (text: string) => void;
    onSubmit: (
      text: string,
      mode: "message" | "command",
      images?: File[]
    ) => Promise<boolean>;
    onPause?: () => Promise<void>;
    onError?: (error: unknown | null) => void;
  } = $props();

  let text = $state("");
  let lastInitialText = $state<string | null>(null);
  let menuOpen = $state(false);
  let busy = $state(false);
  let pendingImages = $state<PendingImage[]>([]);
  let imageInput = $state<HTMLInputElement | null>(null);
  let selectedClarificationChoice = $state<string | null>(null);
  let lastClarificationRequestId = $state<string | null>(null);
  let draftBeforeClarification = $state<string | null>(null);
  let nextImageId = 1;
  let dragDepth = 0;
  let draggingImages = $state(false);

  function canonical(raw: string): string {
    const first = raw.split(/\s+/)[0].toLowerCase();
    return catalog?.canon?.[first] || first;
  }

  function commandKind(name: string): "command" | "skill" | "sub" | "exit" | null {
    if (!catalog) return null;
    if (catalog.skills.some((pair) => pair[0] === name)) return "skill";
    const category = catalog.categories.find((cat) => cat.pairs.some((pair) => pair[0] === name));
    if (category?.name === "Exit") return "exit";
    if (Object.prototype.hasOwnProperty.call(catalog.sub || {}, name)) return "sub";
    if (category) return "command";
    return null;
  }

  function commandFor(raw: string): string | null {
    if (!raw.startsWith("/") || !catalog) return null;
    const first = raw.split(/\s+/)[0];
    const name = canonical(raw);
    const kind = commandKind(name);
    if (kind !== "skill" && kind !== "command" && kind !== "sub") return null;
    return name + raw.slice(first.length);
  }

  function matches(pair: [string, string], query: string): boolean {
    if (!query) return true;
    if (pair[0].toLowerCase().includes(query)) return true;
    if (pair[1].toLowerCase().includes(query)) return true;
    return catalog?.canon?.[`/${query}`] === pair[0];
  }

  let menuItems = $derived.by<MenuItem[]>(() => {
    if (!catalog || !/^\/\S*$/.test(text)) return [];
    const query = text.slice(1).toLowerCase();
    const items: MenuItem[] = [];
    for (const category of catalog.categories || []) {
      if (category.name === "Exit") continue;
      for (const pair of category.pairs || []) {
        if (matches(pair, query)) items.push({ name: pair[0], description: pair[1], skill: false });
      }
    }
    for (const pair of catalog.skills || []) {
      if (matches(pair, query)) items.push({ name: pair[0], description: pair[1], skill: true });
    }
    return items;
  });

  $effect(() => {
    menuOpen = menuItems.length > 0;
  });

  $effect(() => {
    if (initialText !== lastInitialText) {
      lastInitialText = initialText;
      if (!pendingClarification) text = initialText;
    }
  });

  $effect(() => {
    const requestId = pendingClarification?.request_id || null;
    if (requestId !== lastClarificationRequestId) {
      const previousRequestId = lastClarificationRequestId;
      lastClarificationRequestId = requestId;
      selectedClarificationChoice = null;
      if (requestId) {
        if (!previousRequestId) draftBeforeClarification = text;
        text = "";
      } else {
        text = draftBeforeClarification ?? initialText;
        draftBeforeClarification = null;
      }
    }
  });

  function inputChanged(): void {
    if (pendingClarification) {
      if (text.trim()) selectedClarificationChoice = null;
      return;
    }
    onDraft?.(text);
  }

  async function send(raw = text): Promise<void> {
    const trimmed = raw.trim();
    if ((!trimmed && pendingImages.length === 0) || busy || disabled || submitDisabled) return;
    const command = trimmed ? commandFor(trimmed) : null;
    const images = command ? [] : pendingImages;
    busy = true;
    menuOpen = false;
    text = "";
    onDraft?.("");
    try {
      const started = await onSubmit(
        command || trimmed,
        command ? "command" : "message",
        pendingChatImageFiles(images)
      );
      if (started && images.length > 0) {
        pendingImages = clearSentPendingChatImages(
          pendingImages,
          images.map((image) => image.id),
          URL.revokeObjectURL
        );
        if (imageInput) imageInput.value = "";
      }
    } finally {
      busy = false;
    }
  }

  async function sendClarification(): Promise<void> {
    if (!pendingClarification || busy || disabled) return;
    const answer = text.trim() || selectedClarificationChoice || "";
    if (!answer || submitDisabled) return;
    busy = true;
    menuOpen = false;
    try {
      const submitted = await onSubmit(answer, "message", []);
      if (submitted) {
        text = "";
        selectedClarificationChoice = null;
      }
    } finally {
      busy = false;
    }
  }

  function intakeFiles(files: Iterable<File> | ArrayLike<File> | null | undefined): void {
    const { accepted, rejected, nextId } = createPendingChatImages(
      files,
      nextImageId,
      URL.createObjectURL
    );
    nextImageId = nextId;
    if (accepted.length === 0 && rejected.length === 0) return;
    if (rejected.length > 0) {
      if (imageInput) imageInput.value = "";
      onError?.(new Error("Choose an image file."));
    }
    if (accepted.length === 0) return;
    pendingImages = [...pendingImages, ...accepted];
    if (rejected.length === 0) onError?.(null);
  }

  function imageChanged(): void {
    intakeFiles(imageInput?.files);
    if (imageInput) imageInput.value = "";
  }

  function removeImage(image: PendingImage): void {
    pendingImages = removePendingChatImage(pendingImages, image.id, URL.revokeObjectURL);
  }

  function hasImageTransfer(event: DragEvent): boolean {
    return Array.from(event.dataTransfer?.items || []).some((item) =>
      item.type.toLowerCase().startsWith("image/")
    );
  }

  function onDragEnter(event: DragEvent): void {
    if (disabled || busy || !hasImageTransfer(event)) return;
    event.preventDefault();
    dragDepth += 1;
    draggingImages = true;
  }

  function onDragOver(event: DragEvent): void {
    if (disabled || busy || !hasImageTransfer(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    draggingImages = true;
  }

  function onDragLeave(): void {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) draggingImages = false;
  }

  function onDrop(event: DragEvent): void {
    if (disabled || busy) return;
    event.preventDefault();
    dragDepth = 0;
    draggingImages = false;
    intakeFiles(event.dataTransfer?.files);
  }

  function onPaste(event: ClipboardEvent): void {
    const files = event.clipboardData?.files;
    if (!files || files.length === 0) return;
    event.preventDefault();
    intakeFiles(files);
  }

  async function activateButton(): Promise<void> {
    if (pendingClarification) {
      await sendClarification();
      return;
    }
    if (pauseMode) {
      if (busy || disabled || pauseDisabled || pausePending || !onPause) return;
      await onPause();
      return;
    }
    await send();
  }

  function choose(item: MenuItem): void {
    const kind = commandKind(item.name);
    if (kind === "skill" || kind === "command") {
      void send(item.name);
      return;
    }
    text = `${item.name} `;
    inputChanged();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (pendingClarification) {
        void sendClarification();
        return;
      }
      if (pauseMode) return;
      void send();
    }
    if (event.key === "Escape") menuOpen = false;
  }

  onDestroy(() => {
    revokePendingChatImages(pendingImages, URL.revokeObjectURL);
  });
</script>

<div
  class={`chat-box${draggingImages ? " drag" : ""}`}
  data-chat-composer
  data-chat-drag-active={draggingImages ? "true" : undefined}
  role="group"
  aria-label="Chat composer"
  ondragenter={onDragEnter}
  ondragover={onDragOver}
  ondragleave={onDragLeave}
  ondrop={onDrop}
>
  {#if pendingClarification}
    <div class="chat-clarification" data-chat-clarification>
      <div
        id="chat-clarification-question"
        class="chat-clarification-question"
        data-chat-clarification-question
      >
        {pendingClarification.question}
      </div>
      {#if pendingClarification.choices.length > 0}
        <div
          class="chat-clarification-choices"
          data-chat-clarification-choices
          role="radiogroup"
          aria-labelledby="chat-clarification-question"
        >
          {#each pendingClarification.choices as choice}
            <button
              type="button"
              class:chat-clarification-choice--selected={selectedClarificationChoice === choice}
              class="chat-clarification-choice"
              data-chat-clarification-choice
              role="radio"
              aria-checked={selectedClarificationChoice === choice}
              disabled={disabled || busy}
              onclick={() => {
                selectedClarificationChoice = choice;
                text = "";
              }}
            >
              {choice}
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
  {#if pendingImages.length > 0}
    <div class="chat-image-previews" data-chat-image-previews aria-label="Pending images">
      {#each pendingImages as image, index (image.id)}
        <div
          class="chat-image-preview"
          data-chat-image-preview
          data-chat-image-name={image.file.name}
        >
          <img src={image.url} alt="" />
          <button
            type="button"
            class="chat-image-remove"
            data-chat-image-remove
            aria-label={`Remove image ${index + 1}: ${image.file.name}`}
            title="Remove image"
            disabled={disabled || busy}
            onclick={() => removeImage(image)}
          >
            ×
          </button>
        </div>
      {/each}
    </div>
  {/if}
  <textarea
    class="chat-ta"
    data-chat-input
    rows="1"
    placeholder={pendingClarification ? "Answer the worker…" : placeholder}
    aria-labelledby={pendingClarification ? "chat-clarification-question" : undefined}
    bind:value={text}
    disabled={disabled || busy}
    oninput={inputChanged}
    onkeydown={onKeydown}
    onpaste={onPaste}
  ></textarea>
  <div class="chat-foot">
    {#if !pendingClarification}
      <button
        type="button"
        class="chat-slash"
        data-chat-slash
        onclick={() => {
          if (!text.startsWith("/")) text = `/${text}`;
          inputChanged();
        }}
      >
        /
      </button>
      <button
        type="button"
        class={`chat-image${pendingImages.length ? " on" : ""}`}
        data-chat-image
        data-chat-image-pending={pendingImages.length ? "true" : undefined}
        data-chat-image-count={pendingImages.length || undefined}
        disabled={disabled || busy}
        aria-label={pendingImages.length ? "Attach more images" : "Attach images"}
        title={pendingImages.length ? `${pendingImages.length} image selected` : "Attach images"}
        onclick={() => imageInput?.click()}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2.5 3.5h11v9h-11zM4 10l2.5-2.5 2 2 1.5-1.5 2 2M10.5 6h.01" />
        </svg>
      </button>
      <input
        bind:this={imageInput}
        class="chat-image-input"
        data-chat-image-input
        type="file"
        accept="image/*"
        multiple
        onchange={imageChanged}
      />
    {/if}
    <button
      type="button"
      class={`chat-send${text.trim() || pendingImages.length || pauseMode || selectedClarificationChoice ? " on" : ""}${pauseMode && !pendingClarification ? " pause" : ""}`}
      data-chat-send
      disabled={pendingClarification ? (disabled || submitDisabled || busy || (!text.trim() && !selectedClarificationChoice)) : (pauseMode ? (disabled || pauseDisabled || pausePending || busy) : (disabled || submitDisabled || busy || (!text.trim() && pendingImages.length === 0)))}
      onclick={() => void activateButton()}
      title={pendingClarification ? "Answer" : (pauseMode ? "Pause" : "Send")}
    >
      {pauseMode && !pendingClarification ? "Ⅱ" : "↑"}
    </button>
  </div>
  <div class="chat-menu" data-chat-menu hidden={!menuOpen}>
    {#each menuItems as item}
      <button
        type="button"
        class="chat-menu-item"
        data-chat-cmd={item.name}
        data-chat-skill={item.skill ? "" : undefined}
        onmousedown={(event) => event.preventDefault()}
        onclick={() => choose(item)}
      >
        <span class="chat-menu-name">{item.name}</span>
        <span class="chat-menu-desc">{item.description}</span>
      </button>
    {/each}
  </div>
  <div class="chat-drop-label" data-chat-drop-label aria-hidden="true">Drop images to attach</div>
</div>
