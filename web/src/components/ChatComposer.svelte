<script lang="ts">
  import type { CommandCatalog } from "../lib/types";

  type MenuItem = { name: string; description: string; skill: boolean };

  let {
    catalog,
    disabled = false,
    submitDisabled = false,
    pauseMode = false,
    pauseDisabled = false,
    pausePending = false,
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
    initialText?: string;
    placeholder?: string;
    onDraft?: (text: string) => void;
    onSubmit: (
      text: string,
      mode: "message" | "command",
      image?: File
    ) => Promise<boolean>;
    onPause?: () => Promise<void>;
    onError?: (error: unknown | null) => void;
  } = $props();

  let text = $state("");
  let lastInitialText = $state<string | null>(null);
  let menuOpen = $state(false);
  let busy = $state(false);
  let pendingImage = $state<File | null>(null);
  let imageInput = $state<HTMLInputElement | null>(null);

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
      text = initialText;
    }
  });

  function inputChanged(): void {
    onDraft?.(text);
  }

  async function send(raw = text): Promise<void> {
    const trimmed = raw.trim();
    if ((!trimmed && !pendingImage) || busy || disabled || submitDisabled) return;
    const command = trimmed ? commandFor(trimmed) : null;
    const image = command ? undefined : pendingImage || undefined;
    busy = true;
    menuOpen = false;
    text = "";
    onDraft?.("");
    try {
      const started = await onSubmit(command || trimmed, command ? "command" : "message", image);
      if (started && image === pendingImage) {
        pendingImage = null;
        if (imageInput) imageInput.value = "";
      }
    } finally {
      busy = false;
    }
  }

  function imageChanged(): void {
    const file = imageInput?.files?.[0] || null;
    if (!file) return;
    if (file.type && !file.type.toLowerCase().startsWith("image/")) {
      if (imageInput) imageInput.value = "";
      onError?.(new Error("Choose an image file."));
      return;
    }
    pendingImage = file;
    onError?.(null);
  }

  async function activateButton(): Promise<void> {
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
      if (pauseMode) return;
      void send();
    }
    if (event.key === "Escape") menuOpen = false;
  }
</script>

<div class="chat-box">
  <textarea
    class="chat-ta"
    data-chat-input
    rows="1"
    {placeholder}
    bind:value={text}
    disabled={disabled || busy}
    oninput={inputChanged}
    onkeydown={onKeydown}
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
      class={`chat-image${pendingImage ? " on" : ""}`}
      data-chat-image
      data-chat-image-pending={pendingImage ? "true" : undefined}
      disabled={disabled || busy}
      aria-label="Attach image"
      title={pendingImage ? `Image selected: ${pendingImage.name}` : "Attach image"}
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
      onchange={imageChanged}
    />
    <button
      type="button"
      class={`chat-send${text.trim() || pendingImage || pauseMode ? " on" : ""}${pauseMode ? " pause" : ""}`}
      data-chat-send
      disabled={pauseMode ? (disabled || pauseDisabled || pausePending || busy) : (disabled || submitDisabled || busy || (!text.trim() && !pendingImage))}
      onclick={() => void activateButton()}
      title={pauseMode ? "Pause" : "Send"}
    >
      {pauseMode ? "Ⅱ" : "↑"}
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
</div>
