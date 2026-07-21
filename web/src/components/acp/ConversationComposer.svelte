<script lang="ts">
  import type { AvailableCommand } from "@agentclientprotocol/sdk";
  import { onDestroy } from "svelte";
  import type { TurnDeliveryChoice } from "../../lib/acp/contracts";
  import type { DeepReadonly } from "../../lib/acp/conversationState";
  import {
    clearSentPendingConversationImages,
    createPendingConversationImages,
    pendingConversationImageFiles,
    removePendingConversationImage,
    revokePendingConversationImages
  } from "../../lib/acp/pendingConversationImages.js";

  type MenuItem = { name: string; description: string };
  type PendingImage = { id: number; file: File; url: string };

  let {
    commands,
    disabled = false,
    submitDisabled = false,
    initialText = "",
    placeholder = "Message the employee...",
    active = false,
    supportsSteer = true,
    deliveryChoice = "queue",
    onDeliveryChoice,
    onStop,
    onDraft,
    onSubmit,
    onError
  }: {
    commands: readonly DeepReadonly<AvailableCommand>[];
    disabled?: boolean;
    submitDisabled?: boolean;
    initialText?: string;
    placeholder?: string;
    active?: boolean;
    supportsSteer?: boolean;
    deliveryChoice?: TurnDeliveryChoice;
    onDeliveryChoice?: (choice: TurnDeliveryChoice) => void;
    onStop?: () => void;
    onDraft?: (text: string) => void;
    onSubmit: (
      text: string,
      mode: "message" | "command",
      images?: File[]
    ) => Promise<boolean>;
    onError?: (error: unknown | null) => void;
  } = $props();

  let text = $state("");
  let lastInitialText = $state<string | null>(null);
  let menuOpen = $state(false);
  let busy = $state(false);
  let pendingImages = $state<PendingImage[]>([]);
  let imageInput = $state<HTMLInputElement | null>(null);
  let nextImageId = 1;
  let dragDepth = 0;
  let draggingImages = $state(false);

  function commandTrigger(name: string): string {
    return name.startsWith("/") ? name : `/${name}`;
  }

  function commandFor(raw: string): string | null {
    if (!raw.startsWith("/")) return null;
    const first = raw.split(/\s+/)[0];
    const command = commands.find(
      (candidate) => commandTrigger(candidate.name).toLowerCase() === first.toLowerCase()
    );
    return command ? commandTrigger(command.name) + raw.slice(first.length) : null;
  }

  let menuItems = $derived.by<MenuItem[]>(() => {
    if (!/^\/\S*$/.test(text)) return [];
    const query = text.slice(1).toLowerCase();
    return commands
      .map((command) => ({
        name: commandTrigger(command.name),
        description: command.description
      }))
      .filter((item) =>
        !query ||
        item.name.slice(1).toLowerCase().includes(query) ||
        item.description.toLowerCase().includes(query)
      );
  });

  $effect(() => {
    menuOpen = menuItems.length > 0;
  });

  $effect(() => {
    if (initialText !== lastInitialText) {
      lastInitialText = initialText;
      text = initialText;
    }
  });

  function inputChanged(): void {
    onDraft?.(text);
  }

  async function send(raw = text): Promise<void> {
    const trimmed = raw.trim();
    if ((!trimmed && pendingImages.length === 0) || busy || disabled || submitDisabled) return;
    const command = trimmed ? commandFor(trimmed) : null;
    const images = command ? [] : pendingImages;
    busy = true;
    menuOpen = false;
    try {
      const started = await onSubmit(
        command || trimmed,
        command ? "command" : "message",
        pendingConversationImageFiles(images)
      );
      if (!started) return;
      text = "";
      onDraft?.("");
      if (images.length > 0) {
        pendingImages = clearSentPendingConversationImages(
          pendingImages,
          images.map((image) => image.id),
          URL.revokeObjectURL
        );
        if (imageInput) imageInput.value = "";
      }
      onError?.(null);
    } catch (error) {
      onError?.(error);
    } finally {
      busy = false;
    }
  }

  function intakeFiles(files: Iterable<File> | ArrayLike<File> | null | undefined): void {
    const { accepted, rejected, nextId } = createPendingConversationImages(
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
    pendingImages = removePendingConversationImage(
      pendingImages,
      image.id,
      URL.revokeObjectURL
    );
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

  function choose(item: MenuItem): void {
    void send(item.name);
  }

  function onSendClick(): void {
    if (active) onStop?.();
    else void send();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
    if (event.key === "Escape") menuOpen = false;
  }

  onDestroy(() => {
    revokePendingConversationImages(pendingImages, URL.revokeObjectURL);
  });
</script>

<div
  class={`chat-box${draggingImages ? " drag" : ""}`}
  data-conversation-composer
  data-chat-drag-active={draggingImages ? "true" : undefined}
  role="group"
  aria-label="Conversation composer"
  ondragenter={onDragEnter}
  ondragover={onDragOver}
  ondragleave={onDragLeave}
  ondrop={onDrop}
>
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
    {placeholder}
    bind:value={text}
    disabled={disabled || busy}
    oninput={inputChanged}
    onkeydown={onKeydown}
    onpaste={onPaste}
  ></textarea>
  <div class="chat-foot">
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
    {#if active}
      <div class="chat-seg" data-chat-delivery role="group" aria-label="Delivery">
        <button
          type="button"
          class:on={deliveryChoice === "queue"}
          aria-pressed={deliveryChoice === "queue"}
          onclick={() => onDeliveryChoice?.("queue")}
        >queue</button>
        <button
          type="button"
          class:on={deliveryChoice === "send_now"}
          aria-pressed={deliveryChoice === "send_now"}
          onclick={() => onDeliveryChoice?.("send_now")}
        >send now</button>
        <button
          type="button"
          class:on={deliveryChoice === "steer"}
          aria-pressed={deliveryChoice === "steer"}
          disabled={!supportsSteer}
          title={supportsSteer ? "Steer the active turn" : "Steer is unavailable for this employee"}
          onclick={() => onDeliveryChoice?.("steer")}
        >steer</button>
      </div>
    {/if}
    <button
      type="button"
      class={`chat-send${active ? " stop" : text.trim() || pendingImages.length ? " on" : ""}`}
      data-chat-send={active ? undefined : true}
      data-chat-stop={active ? true : undefined}
      disabled={active ? false : disabled || submitDisabled || busy || (!text.trim() && pendingImages.length === 0)}
      onclick={onSendClick}
      title={active ? "Stop the turn" : "Send"}
      aria-label={active ? "Stop the turn" : undefined}
    >
      {active ? "■" : "↑"}
    </button>
  </div>
  <div class="chat-menu" data-chat-menu hidden={!menuOpen}>
    {#each menuItems as item}
      <button
        type="button"
        class="chat-menu-item"
        data-chat-cmd={item.name}
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
