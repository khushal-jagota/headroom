<script lang="ts">
  let { open = $bindable(false), root = $bindable(null), disabled, fileCount, onFile, onCommand }: {
    open?: boolean;
    root?: HTMLDivElement | null;
    disabled: boolean;
    fileCount: number;
    onFile: () => void;
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
    aria-label="Attach a file or add a command"
    title="Attach a file or add a command"
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
