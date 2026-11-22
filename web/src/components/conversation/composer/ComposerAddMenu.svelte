<script lang="ts">
  let { open = $bindable(false), root = $bindable(null), disabled, imageCount, fileCount, onPicture, onFile, onCamera, onCommand }: {
    open?: boolean;
    root?: HTMLDivElement | null;
    disabled: boolean;
    imageCount: number;
    fileCount: number;
    onPicture: () => void;
    onFile: () => void;
    onCamera: () => void;
    onCommand: () => void;
  } = $props();
</script>

<div class="chat-add" bind:this={root}>
  <button
    type="button"
    class="chat-image chat-add-btn"
    data-conversation-add
    aria-haspopup="menu"
    aria-expanded={open}
    aria-label="Add a picture, a file, a photo, or a command"
    title="Add a picture, a file, a photo, or a command"
    disabled={disabled}
    onclick={() => (open = !open)}
  >
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  </button>
  {#if open}
    <div class="chat-add-menu" role="menu">
      <button
        type="button"
        class="chat-add-item"
        role="menuitem"
        disabled={disabled}
        data-conversation-image
        data-conversation-image-count={imageCount || undefined}
        onclick={() => { open = false; onPicture(); }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4.5 6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2zM4.5 16l4.2-4.2a1.5 1.5 0 0 1 2.1 0l4.7 4.7M13.5 14.5l1.2-1.2a1.5 1.5 0 0 1 2.1 0l2.7 2.7M14.5 9h.01" />
        </svg>
        <span>Picture</span>
        {#if imageCount > 0}<span class="chat-add-count">{imageCount}</span>{/if}
      </button>
      <button
        type="button"
        class="chat-add-item"
        role="menuitem"
        disabled={disabled}
        data-conversation-file
        data-conversation-file-count={fileCount || undefined}
        onclick={() => { open = false; onFile(); }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M8 12.5l6.8-6.8a3 3 0 0 1 4.2 4.2l-8.2 8.2a5 5 0 0 1-7.1-7.1l8-8" />
        </svg>
        <span>File</span>
        {#if fileCount > 0}<span class="chat-add-count">{fileCount}</span>{/if}
      </button>
      <button
        type="button"
        class="chat-add-item"
        role="menuitem"
        disabled={disabled}
        data-conversation-camera
        onclick={() => { open = false; onCamera(); }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3.5 8.5a2 2 0 0 1 2-2h2l1.5-2h6l1.5 2h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" />
        </svg>
        <span>Take a picture</span>
      </button>
      <!-- The cursor stays in the box: everything this does is done to what is
           being written there. -->
      <button
        type="button"
        class="chat-add-item"
        role="menuitem"
        disabled={disabled}
        data-conversation-slash
        onmousedown={(event) => event.preventDefault()}
        onclick={() => { open = false; onCommand(); }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 4.5l-5 15" /></svg>
        <span>Write a command</span>
      </button>
    </div>
  {/if}
</div>

<style>
  /* --- the add menu: a Surface floating with a shadow, upward from the +, its left edge on the
     +'s and its bottom 8px above the composer's top edge; its rows are quiet pill buttons. */
  .chat-add { position: relative; display: inline-flex; }
  .chat-add-menu {
    position: absolute;
    z-index: 40;
    inset-block-end: calc(100% + var(--cv-s2) + var(--c2-menu-lift, 0px));
    inset-inline-start: 0;
    min-width: 220px;
    display: flex;
    flex-direction: column;
    padding: 6px;
    background: var(--cv-surface);
    border: 1px solid var(--cv-surface-edge);
    border-radius: var(--cv-surface-radius);
    box-shadow: var(--cv-shadow-float);
    animation: chat-pop-in var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes chat-pop-in {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: none; }
  }
  .chat-add-item {
    display: flex;
    align-items: center;
    width: 100%;
    gap: 6px;
    min-height: 32px;
    padding: 6px 14px;
    background: none;
    border: none;
    border-radius: var(--cv-r-pill);
    color: var(--cv-quiet-ink);
    font-family: var(--cv-font);
    font-size: var(--cv-type-control);
    font-weight: var(--cv-weight-label);
    line-height: var(--cv-leading-control);
    text-align: left;
    cursor: pointer;
  }
  .chat-add-item:hover:not(:disabled),
  .chat-add-item:focus-visible { color: var(--cv-ink); background: #2a2a2a; }
  .chat-add-item:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: var(--cv-focus-offset); }
  .chat-add-item:disabled { color: var(--cv-t3); cursor: default; }
  .chat-add-item svg {
    width: 16px;
    height: 16px;
    flex: none;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    color: var(--cv-quiet-ink);
  }
  .chat-add-item:hover svg { color: var(--cv-ink); }
  .chat-add-count {
    margin-inline-start: auto;
    font-size: var(--cv-type-caption);
    font-variant-numeric: tabular-nums;
    color: var(--cv-t3);
  }
  @media (prefers-reduced-motion: reduce) { .chat-add-menu { animation: none; } }
</style>
