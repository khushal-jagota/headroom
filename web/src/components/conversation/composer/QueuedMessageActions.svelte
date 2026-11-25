<script lang="ts">
  import { tick } from "svelte";
  import { floatingPanel } from "../../../lib/floatingPanel";

  let {
    heldPromptId,
    showSteer = false,
    running = false,
    onDiscard,
    onPromote,
    onEdit
  }: {
    heldPromptId: string;
    showSteer?: boolean;
    running?: boolean;
    onDiscard?: () => Promise<unknown> | void;
    onPromote?: (mode: "send_now" | "steer") => Promise<unknown> | void;
    onEdit?: () => Promise<unknown> | void;
  } = $props();

  let anchor = $state<HTMLSpanElement>();
  let trigger = $state<HTMLButtonElement>();
  let menu = $state<HTMLDivElement>();
  let open = $state(false);
  let busy = $state(false);
  let failed = $state(false);
  const menuId = $props.id();
  const canAct = $derived(Boolean(onPromote || onEdit || onDiscard));

  function menuItems(): HTMLButtonElement[] {
    return Array.from(menu?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
  }

  async function showMenu(last = false): Promise<void> {
    if (!canAct || busy) return;
    open = true;
    await tick();
    if (open) menuItems().at(last ? -1 : 0)?.focus({ preventScroll: true });
  }

  function closeMenu(restoreFocus = false): void {
    open = false;
    if (restoreFocus) trigger?.focus({ preventScroll: true });
  }

  function moveMenuFocus(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = menuItems();
    if (!items.length) return;
    event.preventDefault();
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    let index = 0;
    if (event.key === "End") index = items.length - 1;
    if (event.key === "ArrowDown") index = (current + 1) % items.length;
    if (event.key === "ArrowUp") index = current > 0 ? current - 1 : items.length - 1;
    items[index]?.focus({ preventScroll: true });
  }

  $effect(() => {
    if (!open) return;
    const closeFromOutside = (event: PointerEvent): void => {
      if (!(event.target instanceof Node) || !anchor?.contains(event.target)) closeMenu();
    };
    window.addEventListener("pointerdown", closeFromOutside, true);
    return () => window.removeEventListener("pointerdown", closeFromOutside, true);
  });

  async function act(action: (() => Promise<unknown> | void) | undefined): Promise<void> {
    if (busy || !action) return;
    const actionHadFocus = menu?.contains(document.activeElement) ?? false;
    busy = true;
    failed = false;
    closeMenu();
    try {
      await action();
    } catch {
      failed = true;
    } finally {
      busy = false;
      if (actionHadFocus) {
        await tick();
        if (document.activeElement === document.body) trigger?.focus({ preventScroll: true });
      }
    }
  }
</script>

{#if canAct}
  <span class="chat-qrow-anchor" bind:this={anchor}>
    <button
      type="button"
      class="chat-qrow-more"
      data-conversation-held-more
      aria-haspopup="menu"
      aria-controls={menuId}
      aria-expanded={open}
      aria-label="Queued message actions"
      title="More"
      disabled={busy}
      bind:this={trigger}
      onclick={() => open ? closeMenu() : void showMenu()}
      onkeydown={(event) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        event.preventDefault();
        void showMenu(event.key === "ArrowUp");
      }}
    ><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.25" /><circle cx="8" cy="8" r="1.25" /><circle cx="12.5" cy="8" r="1.25" /></svg></button>
    <div
      bind:this={menu}
      id={menuId}
      class="chat-qrow-menu"
      role="menu"
      tabindex="-1"
      aria-label="Queued message"
      hidden={!open}
      popover="manual"
      {@attach open && floatingPanel(trigger, "top-end")}
      data-conversation-held-menu
      onkeydown={moveMenuFocus}
      onfocusout={(event) => {
        if (event.relatedTarget instanceof Node && !anchor?.contains(event.relatedTarget)) closeMenu();
      }}
    >
      {#if onPromote}
        <button
          type="button"
          role="menuitem"
          tabindex="-1"
          class="chat-qrow-item"
          data-conversation-held-promote="send_now"
          data-held-prompt-id={heldPromptId}
          title={running ? "Stop the running turn and run this next" : "Run this next"}
          disabled={busy}
          onclick={() => void act(() => onPromote?.("send_now"))}
        >Send now</button>
        {#if showSteer}
          <button
            type="button"
            role="menuitem"
            tabindex="-1"
            class="chat-qrow-item"
            data-conversation-held-promote="steer"
            data-held-prompt-id={heldPromptId}
            title="Put this into the turn that is already running"
            disabled={busy || !running}
            onclick={() => { if (showSteer && running) void act(() => onPromote?.("steer")); }}
          >Steer now</button>
        {/if}
      {/if}
      {#if onEdit}
        <button
          type="button"
          role="menuitem"
          tabindex="-1"
          class="chat-qrow-item"
          data-conversation-held-edit={heldPromptId}
          title="Take this message out of the queue and back into the composer"
          disabled={busy}
          onclick={() => void act(onEdit)}
        >Edit</button>
      {/if}
      {#if onDiscard}
        <button
          type="button"
          role="menuitem"
          tabindex="-1"
          class="chat-qrow-item"
          data-conversation-held-discard={heldPromptId}
          aria-label="Remove this message from the queue"
          title="Remove from the queue"
          disabled={busy}
          onclick={() => void act(onDiscard)}
        >Remove</button>
      {/if}
    </div>
  </span>
  {#if failed}<span class="chat-qrow-error" role="status">Could not change this queued message.</span>{/if}
{/if}

<style>
  .chat-qrow-anchor { position: relative; display: inline-flex; }
  .chat-qrow-more {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 0;
    border-radius: var(--cv-r-pill);
    background: none;
    color: #c4c4c4;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-qrow-more svg { width: 16px; height: 16px; fill: currentColor; }
  .chat-qrow-more:hover:not(:disabled) { background: var(--cv-control); color: var(--cv-ink); }
  .chat-qrow-more[aria-expanded="true"], .chat-qrow-more[aria-expanded="true"]:hover:not(:disabled) {
    position: relative;
    z-index: 31;
    background: var(--cv-control-edge);
    color: var(--cv-t1);
  }
  .chat-qrow-more:disabled { cursor: default; }
  .chat-qrow-more:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; }
  .chat-qrow-menu[hidden] { display: none; }
  .chat-qrow-menu {
    width: max-content;
    min-width: 160px;
    display: grid;
    padding: 6px;
    border: 1px solid var(--cv-surface-edge);
    border-radius: var(--cv-surface-radius);
    background: var(--cv-surface);
    box-shadow: var(--cv-shadow-float);
    animation: chat-qrow-pop var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes chat-qrow-pop { from { opacity: 0; transform: translateY(4px); } }
  .chat-qrow-item {
    display: flex;
    align-items: center;
    min-height: 32px;
    padding: 0 14px;
    border: 0;
    border-radius: var(--cv-r-pill);
    background: none;
    color: var(--cv-ink);
    font: var(--cv-weight-label) var(--cv-type-control)/var(--cv-leading-control) var(--cv-font);
    text-align: start;
    white-space: nowrap;
    cursor: pointer;
  }
  .chat-qrow-item:hover:not(:disabled), .chat-qrow-item:focus-visible { background: var(--cv-control); outline: none; }
  .chat-qrow-item:disabled { color: var(--cv-t3); cursor: default; }
  .chat-qrow-error { color: var(--cv-t2); }
  @media (prefers-reduced-motion: reduce) {
    .chat-qrow-menu { animation: none; }
  }
</style>
