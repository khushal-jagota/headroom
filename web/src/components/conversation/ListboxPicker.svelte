<script lang="ts">
  import { tick, type Snippet } from "svelte";
  import { nextEnabledIndex, pickerRoom, placePickerPanel, type PickerPlacement } from "../../lib/pickerPlacement";

  export type ListboxPickerItem = {
    value: string;
    name: string;
    /** Shown, but not a choice. The approval picker uses this for stages that have gone by. */
    disabled?: boolean;
  };

  export type ListboxPageWindow = {
    offset: number; total: number; rowHeight: number; activeIndex?: number;
    onWindow: (index: number) => void;
    onTypeahead: (prefix: string, after: number) => void;
  };

  export type ListboxPickerController = {
    open: () => void;
    readonly expanded: boolean;
    readonly controls: string | undefined;
    close: (returnFocus?: boolean) => void;
    focusList: () => void;
    setActiveValue: (value: string | null) => void;
  };

  let {
    items,
    pageWindow,
    selectedValue,
    disabled = false,
    selectionDisabled = disabled,
    keepOpenWhenDisabled = false,
    label,
    listLabel = label,
    kind = "compact",
    chevron = "typed",
    scrollPanel = false,
    hideTrigger = false,
    below = false,
    fitWithin = null,
    align = "left",
    /** Leave the page's clipping and stacking, and stay inside the viewport. */
    placeInViewport = false,
    /** Remounts the list with a short fade when the value changes. */
    motionKey = undefined,
    anchor = null,
    attributes = {},
    triggerAttributes = {},
    panelAttributes = {},
    optionAttributes,
    panelBusy = false,
    controller = $bindable(),
    onOpen,
    onClose,
    onChoose,
    onActiveChange,
    triggerContent,
    beforeList,
    optionContent,
    afterList
  }: {
    items: readonly ListboxPickerItem[];
    pageWindow?: ListboxPageWindow;
    selectedValue: string | null;
    disabled?: boolean;
    selectionDisabled?: boolean;
    keepOpenWhenDisabled?: boolean;
    label: string;
    listLabel?: string;
    kind?: "rail" | "compact";
    /** How the trigger's chevron is drawn. A typed caret is sized by the font rather
     *  than by its box, so it reads small beside stroked icons. The conversation's
     *  composer asks for the drawn one; every other caller keeps what it had. */
    chevron?: "typed" | "drawn" | "none";
    scrollPanel?: boolean;
    hideTrigger?: boolean;
    below?: boolean;
    /** Keep this picker inside its nearest matching scroll area. */
    fitWithin?: string | null;
    align?: "left" | "right";
    /** Paint the panel in the top layer and keep it inside the viewport. The page clips
     *  and isolates the document on purpose, so a panel anchored inside it cannot rise
     *  over the composer or leave the bottom of an artifact. */
    placeInViewport?: boolean;
    /** Rebuilds the list with a short fade when this value changes. */
    motionKey?: string | number;
    /** Optional second entry point for the same picker; keeps one panel and draft. */
    anchor?: HTMLElement | null;
    attributes?: Record<string, string | undefined>;
    triggerAttributes?: Record<string, string | undefined>;
    panelAttributes?: Record<string, string | undefined>;
    optionAttributes?: (
      item: ListboxPickerItem,
      active: boolean,
      selected: boolean
    ) => Record<string, string | undefined>;
    panelBusy?: boolean;
    controller?: ListboxPickerController;
    onOpen?: () => void;
    /** Every close, dismissal or not. A picker with a draft discards it here. */
    onClose?: () => void;
    onChoose: (value: string) => void;
    /** Optional explanatory preview only; activation still belongs to onChoose. */
    onActiveChange?: (value: string | null) => void;
    triggerContent: Snippet<[boolean]>;
    beforeList?: Snippet;
    optionContent: Snippet<[ListboxPickerItem, boolean]>;
    afterList?: Snippet;
  } = $props();

  const listId = $props.id();
  let open = $state(false);
  let painted = $state(false);
  let closing = $state(false);
  let viewportBox = $state<PickerPlacement | null>(null);
  let closeTimer: ReturnType<typeof setTimeout> | undefined;
  let settling = false;
  let anchorOffset = $state<{left:number;top:number} | null>(null);
  let anchorPositionReady = $state(true);
  let activeIndex = $state(0);
  let root = $state<HTMLDivElement | null>(null);
  let trigger = $state<HTMLButtonElement | null>(null);
  let list = $state<HTMLDivElement | null>(null);
  let panelElement = $state<HTMLDivElement | null>(null);
  let fittedBounds = $state<{left: number; top: number; height: number} | null>(null);
  let panelBounds = $state<{left: number; top: number; height: number} | null>(null);
  let typeahead = "";
  let typeaheadTimer: ReturnType<typeof setTimeout> | undefined;
  let returnFocusWhenEnabled = false;
  // Hover bookkeeping. Plain locals: this must never re-render the list.
  let pointerSpot: { x: number; y: number } | null = null;
  let pointerReallyMoved = false;

  let itemOffset = $derived(pageWindow?.offset ?? 0);
  let totalItems = $derived(pageWindow?.total ?? items.length);
  let active = $derived(totalItems === 0 ? 0 : Math.min(activeIndex, totalItems - 1));
  let localActive = $derived(active-itemOffset);
  $effect(() => { const index=pageWindow?.activeIndex; if(index!==undefined) activeIndex=index; });
  function activate(index:number):void {
    activeIndex=index;
    if(pageWindow && (index<itemOffset || index>=itemOffset+items.length)) pageWindow.onWindow(index);
  }
  function scrollWindow():void {
    if(!pageWindow || !list) return;
    const index=Math.max(0,Math.floor(list.scrollTop/pageWindow.rowHeight));
    if(index<itemOffset || (index+8>=itemOffset+items.length && itemOffset+items.length<totalItems)) pageWindow.onWindow(index);
  }

  $effect(() => {
    onActiveChange?.(open ? items[localActive]?.value ?? null : null);
  });

  function focusList(): void {
    void tick().then(() => list?.focus(anchor || placeInViewport ? {preventScroll:true} : undefined));
  }

  function setActiveValue(value: string | null): void {
    const index = value === null ? -1 : items.findIndex((item) => item.value === value && !item.disabled);
    const fallback = items.findIndex((item) => !item.disabled);
    activate(itemOffset+Math.max(0, index >= 0 ? index : fallback));
    // A rail switch puts different words in the list. Letters typed at the old one must
    // not join letters typed at the new one, or the match silently finds nothing.
    forgetTypeahead();
    focusList();
  }

  function forgetTypeahead(): void {
    typeahead = "";
    if (typeaheadTimer !== undefined) clearTimeout(typeaheadTimer);
  }

  function motionReduced(): boolean {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function cancelCloseTimer(): void {
    if (closeTimer !== undefined) clearTimeout(closeTimer);
    closeTimer = undefined;
  }

  function finishClose(): void {
    cancelCloseTimer();
    if (open) return;
    closing = false;
    painted = false;
  }

  function close(returnFocus = true): void {
    if (returnFocus) (anchor ?? trigger)?.focus();
    const wasOpen = open;
    if (wasOpen) onClose?.();
    open = false;
    forgetTypeahead();
    if (!wasOpen && !painted) return;
    if (!placeInViewport || motionReduced()) {
      cancelCloseTimer();
      closing = false;
      painted = false;
      return;
    }
    if (!painted || closing) return;
    closing = true;
    const panel = panelElement;
    closeTimer = setTimeout(finishClose, 180);
    panel?.addEventListener("animationend", (event) => {
      if (event.target === panel) finishClose();
    }, { once: true });
  }

  controller = { open: () => void openPanel(), close, focusList, setActiveValue, get expanded() { return open; }, get controls() { return open ? listId : undefined; } };

  // Hold focus on the picker until its disabled trigger can receive it again.
  $effect.pre(() => {
    if (disabled && open && !keepOpenWhenDisabled) {
      open = false;
      painted = false;
      closing = false;
      cancelCloseTimer();
      returnFocusWhenEnabled = true;
      root?.focus();
    }
  });

  $effect(() => {
    if (disabled || !returnFocusWhenEnabled) return;
    const focusRemainsInside = root?.contains(document.activeElement) ?? false;
    returnFocusWhenEnabled = false;
    if (focusRemainsInside) trigger?.focus();
  });

  /* A panel that mounts or reflows under a still pointer receives a mouse move at the
   * pointer's resting place. The browser is reporting geometry there, not a person
   * choosing a row, and acting on it takes the active option away from the keyboard.
   * This capture listener runs on the same event, just before the option's own handler,
   * so the handler can tell the two apart by whether the pointer changed place. */
  $effect(() => {
    function trackPointer(event: MouseEvent): void {
      pointerReallyMoved =
        pointerSpot !== null && (event.clientX !== pointerSpot.x || event.clientY !== pointerSpot.y);
      pointerSpot = { x: event.clientX, y: event.clientY };
    }
    document.addEventListener("mousemove", trackPointer, { capture: true, passive: true });
    return () => document.removeEventListener("mousemove", trackPointer, true);
  });

  $effect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent): void {
      const target = event.target;
      const inside = target instanceof Node && ((root !== null && root.contains(target)) || (panelElement !== null && panelElement.contains(target)));
      if (!inside) close(false);
    }
    document.addEventListener("pointerdown", closeOutside, true);
    return () => document.removeEventListener("pointerdown", closeOutside, true);
  });

  $effect(() => {
    if ((anchor || fitWithin) && !anchorPositionReady) return;
    active; itemOffset; items.length;
    list?.querySelector<HTMLElement>("[data-listbox-picker-active]")?.scrollIntoView({ block: "nearest" });
  });

  function positionViewportPanel(): void {
    const entry = anchor ?? trigger;
    const panel = panelElement;
    if (!entry || !root || !panel || !list) return;
    if (typeof panel.showPopover === "function" && !panel.matches(":popover-open")) {
      try { panel.showPopover(); } catch { /* Not connected yet, or already showing. */ }
    }
    const anchorRect = entry.getBoundingClientRect();
    const container = fitWithin ? entry.closest(fitWithin) ?? root.closest(fitWithin) : null;
    const page = entry.closest(".ticket-page") ?? root.closest(".ticket-page");
    const dock = page?.querySelector<HTMLElement>("[data-conversation-layer-host]");
    const dockRect = dock && dock.getClientRects().length ? dock.getBoundingClientRect() : null;
    const area = container?.getBoundingClientRect();
    viewportBox = placePickerPanel({
      anchor: anchorRect,
      panel: {
        width: panel.offsetWidth,
        height: Math.max(list.scrollHeight + panel.offsetHeight - list.clientHeight, panel.querySelector<HTMLElement>(".picker-rail")?.offsetHeight ?? 0)
      },
      boundary: pickerRoom({
        viewportWidth: document.documentElement.clientWidth,
        viewportHeight: window.innerHeight,
        container: area ? { left: area.left, top: area.top, right: area.right, bottom: area.bottom } : null,
        dockTop: dockRect && anchorRect.bottom <= dockRect.top + 1 ? dockRect.top : null
      }),
      prefer: below ? "below" : "above"
    });
  }

  function fitPanel(): void {
    const boundary = fitWithin ? root?.closest(fitWithin) : null;
    const entry = anchor ?? trigger;
    if (!boundary || !root || !entry || !panelElement || !list) return;
    const area = boundary.getBoundingClientRect();
    const origin = root.getBoundingClientRect();
    const button = entry.getBoundingClientRect();
    const panel = panelElement.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(panelElement).getPropertyValue("--space-1")) || 4;
    const top = Math.max(0, area.top);
    // The resting conversation dock can overlay a full-height document.
    // A dock that steps aside for an artifact's own bar is not drawn, and is no limit.
    const dockLayer = boundary.closest(".ticket-page")?.querySelector<HTMLElement>("[data-conversation-layer-host]");
    const dock = dockLayer?.getClientRects().length ? dockLayer.getBoundingClientRect() : undefined;
    const bottom = Math.min(window.innerHeight, area.bottom, dock?.top ?? area.bottom);
    if (button.bottom <= top || button.top >= bottom) { close(false); return; }
    const above = Math.max(0, button.top - top - gap);
    const beneath = Math.max(0, bottom - button.bottom - gap);
    const rail = panelElement.querySelector<HTMLElement>(".picker-rail");
    const naturalHeight = Math.max(list.scrollHeight + panel.height - list.clientHeight, rail?.scrollHeight ?? 0);
    const down = naturalHeight <= beneath || (naturalHeight > above && beneath >= above);
    const height = Math.min(naturalHeight, down ? beneath : above);
    const left = Math.max(0, area.left, Math.min(button.left, Math.min(window.innerWidth, area.right) - panel.width));
    fittedBounds = {left:left-origin.left, top:(down ? button.bottom+gap : button.top-gap-height)-origin.top, height};
  }

  $effect(() => {
    if (!open || !placeInViewport) return;
    items;
    const update = () => positionViewportPanel();
    document.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    void tick().then(update);
    return () => {
      document.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  });

  $effect(() => {
    if (!open || !fitWithin || placeInViewport) return;
    items; // A rail switch changes the natural list height.
    const boundary = root?.closest(fitWithin);
    if (!boundary) return;
    const update = () => fitPanel();
    const observer = new ResizeObserver(update);
    observer.observe(boundary);
    const dock = boundary.closest(".ticket-page")?.querySelector("[data-conversation-layer-host]");
    if (dock) observer.observe(dock);
    boundary.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    void tick().then(update);
    return () => {
      observer.disconnect();
      boundary.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  });

  async function openPanel(): Promise<void> {
    if (disabled) return;
    cancelCloseTimer();
    closing = false;
    settling = true;
    onOpen?.();
    await tick();
    const rootRect = root?.getBoundingClientRect();
    const anchorRect = anchor?.getBoundingClientRect();
    anchorOffset = rootRect && anchorRect ? {left:anchorRect.left-rootRect.left,top:anchorRect.bottom-rootRect.top} : null;
    anchorPositionReady = anchorOffset === null && !fitWithin;
    const selectedIndex = items.findIndex((item) => item.value === selectedValue && !item.disabled);
    const fallbackIndex = items.findIndex((item) => !item.disabled);
    activate(itemOffset+Math.max(0, selectedIndex >= 0 ? selectedIndex : fallbackIndex));
    viewportBox = null;
    painted = true;
    open = true;
    panelBounds = null;
    fittedBounds = null;
    await tick();
    if (scrollPanel && panelElement && trigger) {
      const triggerRect = trigger.getBoundingClientRect();
      const panelRect = panelElement.getBoundingClientRect();
      const dock = trigger.closest("[data-conversation-pane]")?.getBoundingClientRect();
      const bottom = Math.min(window.innerHeight, dock?.bottom ?? window.innerHeight) - 8;
      const top = below ? triggerRect.bottom + 4 : Math.max(8, triggerRect.top - panelRect.height - 4);
      panelBounds = {left: Math.max(8, Math.min(panelRect.left, window.innerWidth - panelRect.width - 8)), top, height: Math.max(48, (below ? bottom : triggerRect.top - 4) - top)};
      await tick();
    }
    // An alternate entry point can sit near the viewport edge. Bound the same
    // panel using its rendered width; its draft, keyboard flow and focus stay here.
    if (anchorOffset && panelElement && root) {
      const panelRect = panelElement.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      const left = Math.max(0,Math.min(panelRect.left,document.documentElement.clientWidth-panelRect.width));
      anchorOffset = { ...anchorOffset, left:left-rootRect.left };
      // Publish the bounded position before focus or the active-row scroll effect.
      await tick();
    }
    try {
      if (placeInViewport) positionViewportPanel();
      else fitPanel();
      await tick();
      anchorPositionReady = true;
      list?.focus(anchor || placeInViewport ? {preventScroll:true} : undefined);
    } finally {
      settling = false;
    }
  }

  function choose(value: string): void {
    if (disabled || selectionDisabled) return;
    if (items.some((item) => item.value === value && item.disabled)) return;
    onChoose(value);
  }

  function onTriggerKeydown(event: KeyboardEvent): void {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    void openPanel();
  }

  function actionButtons(): HTMLButtonElement[] {
    return root === null
      ? []
      : [...root.querySelectorAll<HTMLButtonElement>("[data-listbox-picker-action]:not(:disabled)")];
  }

  function focusAction(index: number): void {
    const actions = actionButtons();
    if (actions.length === 0) return;
    actions[(index + actions.length) % actions.length]?.focus();
  }

  function moveToMatch(key: string): void {
    typeahead += key.toLocaleLowerCase();
    if (typeaheadTimer !== undefined) clearTimeout(typeaheadTimer);
    typeaheadTimer = setTimeout(() => (typeahead = ""), 500);
    if(pageWindow) { pageWindow.onTypeahead(typeahead,active); return; }
    const start = items.length === 0 ? 0 : (active + 1) % items.length;
    for (let offset = 0; offset < items.length; offset += 1) {
      const index = (start + offset) % items.length;
      if (!items[index]?.disabled && items[index]?.name.toLocaleLowerCase().startsWith(typeahead)) {
        activeIndex = index;
        return;
      }
    }
  }

  function onListKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (totalItems === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!pageWindow) {
        const local = nextEnabledIndex(items.map((item) => item.disabled === true), localActive, event.key === "ArrowDown" ? 1 : -1);
        activate(itemOffset + local);
        return;
      }
      const step = event.key === "ArrowDown" ? 1 : totalItems - 1;
      activate((active + step) % totalItems);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      const actions = actionButtons();
      const selectedAction = actions.findIndex((action) => action.getAttribute("aria-pressed") === "true");
      focusAction(selectedAction < 0 ? 0 : selectedAction);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      if (!pageWindow) {
        const flags = items.map((item) => item.disabled === true);
        const index = event.key === "Home" ? flags.findIndex((item) => !item) : flags.findLastIndex((item) => !item);
        activate(itemOffset + Math.max(0, index));
        return;
      }
      activate(event.key === "Home" ? 0 : totalItems - 1);
      return;
    }
    if (event.key === " " && pageWindow && typeahead) {
      event.preventDefault(); moveToMatch(" "); return;
    }
    if ((event.key === "Enter" || event.key === " ") && items[localActive] !== undefined && !items[localActive].disabled) {
      event.preventDefault();
      choose(items[localActive].value);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      moveToMatch(event.key);
    }
  }

  function onPanelKeydown(event: KeyboardEvent): void {
    const target = event.target;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (!(target instanceof HTMLButtonElement) || !target.hasAttribute("data-listbox-picker-action")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      // A rail action can remove its own button (Models does). Keep focus inside
      // the surviving list before that removal, so focusout cannot close the picker.
      list?.focus();
      target.click();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      list?.focus();
      return;
    }
    const actions = actionButtons();
    const index = actions.indexOf(target);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      focusAction(index + (event.key === "ArrowDown" ? 1 : -1));
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      focusAction(event.key === "Home" ? 0 : actions.length - 1);
    }
  }
</script>

{#snippet options()}
  {#if pageWindow}<div aria-hidden="true" style:height={`${itemOffset*pageWindow.rowHeight}px`}></div>{/if}
  {#each items as item, index (item.value)}
    <button
      type="button"
      class="listbox-picker-option"
      class:cursor={index+itemOffset === active && !item.disabled}
      class:on={item.value === selectedValue}
      class:passed={item.disabled}
      id={`${listId}-${index+itemOffset}`}
      role="option"
      aria-posinset={pageWindow ? index+itemOffset+1 : undefined}
      aria-setsize={pageWindow ? totalItems : undefined}
      style:height={pageWindow ? `${pageWindow.rowHeight}px` : undefined}
      tabindex="-1"
      aria-selected={item.value === selectedValue}
      disabled={selectionDisabled}
      data-listbox-picker-value={item.value}
      data-listbox-picker-active={index+itemOffset === active ? "true" : undefined}
      onmousemove={() => { if (pointerReallyMoved && !item.disabled) activeIndex = index+itemOffset; }}
      onmousedown={(event) => event.preventDefault()}
      onclick={() => choose(item.value)}
      {...optionAttributes?.(item, index+itemOffset === active, item.value === selectedValue)}
      aria-disabled={item.disabled ? "true" : undefined}
    >
      {@render optionContent(item, item.value === selectedValue)}
      <span class="listbox-picker-tick" aria-hidden="true">{item.disabled ? "Done" : item.value === selectedValue ? "✓" : ""}</span>
    </button>
  {/each}
  {#if pageWindow}<div aria-hidden="true" style:height={`${Math.max(0,totalItems-itemOffset-items.length)*pageWindow.rowHeight}px`}></div>{/if}
{/snippet}

<div class="listbox-picker" class:scroll-panel={scrollPanel} bind:this={root} tabindex="-1" {...attributes}>
  {#if !hideTrigger}
  <button
    type="button"
    class="listbox-picker-trigger"
    class:is-open={open}
    bind:this={trigger}
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-controls={open ? listId : undefined}
    aria-label={label}
    {disabled}
    onkeydown={onTriggerKeydown}
    onclick={() => (open ? close() : void openPanel())}
    {...triggerAttributes}
  >
    {@render triggerContent(open)}
    {#if chevron === "drawn"}
      <svg class="listbox-picker-chevron-drawn" viewBox="0 0 24 24" aria-hidden="true">
        {#if open}<path d="M6 15l6-6 6 6" />{:else}<path d="M6 9l6 6 6-6" />{/if}
      </svg>
    {:else if chevron === "typed"}
      <span class="listbox-picker-chevron" aria-hidden="true">{open ? "⌃" : "⌄"}</span>
    {/if}
  </button>
  {/if}

  {#if painted}
    <div
      class="listbox-picker-panel"
      bind:this={panelElement}
      class:rail={kind === "rail"}
      class:compact={kind === "compact"}
      class:below
      class:right={align === "right"}
      class:scroll-panel={scrollPanel}
      class:fitted={fittedBounds !== null}
      class:viewport={placeInViewport}
      class:placed={viewportBox !== null}
      class:closing
      popover={placeInViewport ? "manual" : undefined}
      style:position={viewportBox || panelBounds ? "fixed" : undefined}
      style:margin={viewportBox ? "0" : undefined}
      style:inset={viewportBox ? "auto" : undefined}
      style:max-height={viewportBox ? `${viewportBox.maxHeight}px` : panelBounds || fittedBounds ? `${(panelBounds ?? fittedBounds)!.height}px` : undefined}
      style:max-width={viewportBox ? `${viewportBox.maxWidth}px` : undefined}
      style:--picker-max={viewportBox ? `${viewportBox.maxHeight}px` : undefined}
      style:left={viewportBox ? `${viewportBox.left}px` : fittedBounds ? `${fittedBounds.left}px` : panelBounds ? `${panelBounds.left}px` : anchorOffset ? `${anchorOffset.left}px` : undefined}
      style:top={viewportBox ? `${viewportBox.top}px` : fittedBounds ? `${fittedBounds.top}px` : panelBounds ? `${panelBounds.top}px` : anchorOffset ? `calc(${anchorOffset.top}px + var(--space-1))` : undefined}
      style:bottom={viewportBox || fittedBounds || panelBounds || anchorOffset ? "auto" : undefined}
      style:--picker-shift={viewportBox?.side === "below" ? "-6px" : "6px"}
      style:--picker-origin={viewportBox?.side === "above" ? "bottom left" : "top left"}
      {...panelAttributes}
      aria-busy={panelBusy}
      role="presentation"
      onkeydown={onPanelKeydown}
      onfocusout={(event) => {
        if (settling) return;
        const next = event.relatedTarget;
        const inside = next instanceof Node && ((root?.contains(next) ?? false) || (panelElement?.contains(next) ?? false));
        if (!inside) close(false);
      }}
    >
      {#if beforeList}{@render beforeList()}{/if}
      <div
        class="listbox-picker-list"
        bind:this={list}
        id={listId}
        role="listbox"
        aria-label={listLabel}
        aria-activedescendant={items[localActive] === undefined ? undefined : `${listId}-${active}`}
        tabindex="-1"
        onkeydown={onListKeydown}
        onscroll={scrollWindow}
        style:max-height={pageWindow ? '288px' : undefined}
        style:overflow-y={pageWindow ? 'auto' : undefined}
      >
        {#if motionKey !== undefined}
          {#key motionKey}
            <div class="listbox-picker-list-motion">{@render options()}</div>
          {/key}
        {:else}
          {@render options()}
        {/if}
      </div>
      {#if afterList}{@render afterList()}{/if}
    </div>
  {/if}
</div>

<style>
  .listbox-picker { position: relative; display: inline-flex; min-width: 0; }
  .listbox-picker.scroll-panel { max-width: 100%; }
  .listbox-picker.scroll-panel .listbox-picker-trigger { min-height: 24px; }
  .listbox-picker-trigger {
    display: inline-flex; align-items: center; gap: var(--space-2); min-width: 0; max-width: 100%;
    background: transparent; border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-pill); color: var(--text-muted); cursor: pointer;
    font-family: var(--font-mono); font-size: var(--type-xs); line-height: 1.2;
    padding: var(--space-1) var(--space-2);
  }
  .listbox-picker-trigger:hover { border-color: var(--border-color); color: var(--text-strong); }
  .listbox-picker-trigger.is-open { background: var(--surface-2); border-color: var(--border-color); color: var(--text-strong); }
  .listbox-picker-trigger:disabled { cursor: default; opacity: .5; }
  .listbox-picker-chevron { color: var(--text-faintest); flex: none; }
  .listbox-picker-chevron-drawn {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .listbox-picker-panel {
    position: absolute; z-index: 40; bottom: calc(100% + var(--space-1)); left: 0;
    background: var(--surface-2); border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-lg); overflow: hidden;
  }
  .listbox-picker-panel.fitted { min-height:0; grid-template-rows:minmax(0,1fr); }
  .fitted :global(.picker-rail) { min-height:0; overflow:hidden; }
  .fitted > .listbox-picker-list { min-height:0; overflow-y:auto; overscroll-behavior:contain; }
  .listbox-picker-panel.rail {
    display: grid; grid-template-columns: auto minmax(0, 1fr); width: 296px;
    max-width: calc(100vw - var(--space-4) * 2);
  }
  .listbox-picker-panel.compact {
    width: min(148px, calc(100vw - var(--space-4) * 2));
    max-width: calc(100vw - var(--space-4) * 2); padding-block: var(--space-1);
  }
  .listbox-picker-panel.below { top: calc(100% + var(--space-1)); bottom: auto; }
  .listbox-picker-panel.right { right: 0; left: auto; }
  .listbox-picker-panel.scroll-panel { overflow-y: auto; overscroll-behavior: contain; }
  .listbox-picker-list { min-width: 0; padding-block: var(--space-1); outline: none; }
  .compact > .listbox-picker-list { padding-block: 0; }
  .listbox-picker-option {
    display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
    width: 100%; background: transparent; border: 0; color: var(--text-default);
    cursor: pointer; font: inherit; font-size: var(--type-sm); padding: var(--space-2) var(--space-3);
    text-align: left;
  }
  .listbox-picker-option:hover { background-image: var(--interaction-hover); color: var(--text-strong); }
  .listbox-picker-option.cursor { background: var(--surface-recessed); color: var(--text-strong); }
  .listbox-picker-option.on { color: var(--text-strong); }
  .listbox-picker-option.passed { color: var(--text-faintest); cursor: default; }
  .listbox-picker-option.passed .listbox-picker-tick { font-family: var(--font-ui); color: var(--text-faintest); }
  .listbox-picker-option.passed:hover,
  .listbox-picker-option.passed.cursor { background: transparent; background-image: none; color: var(--text-faintest); }
  .listbox-picker-tick { flex: none; font-family: var(--font-mono); font-size: var(--type-xs); }
  .listbox-picker-panel.viewport {
    position: fixed; inset: auto; margin: 0; height: auto; padding: 0; overflow: visible;
    transform-origin: var(--picker-origin, top left);
    border: 0; border-radius: var(--radius-md); box-shadow: var(--shadow-float);
  }
  .listbox-picker-panel.viewport.right { right: auto; }
  .viewport > .listbox-picker-list {
    min-height: 0; max-height: var(--picker-max, none);
    overflow-y: auto; overscroll-behavior: contain;
  }
  .listbox-picker-panel.viewport:not(.placed) { visibility: hidden; }
  .listbox-picker-panel.viewport.placed { animation: picker-panel-in var(--motion-base) var(--motion-ease); }
  .listbox-picker-panel.viewport.closing {
    animation: picker-panel-out var(--motion-base) var(--motion-ease) forwards;
    pointer-events: none;
  }
  .listbox-picker-list-motion { animation: picker-list-in var(--motion-fast) var(--motion-ease); }
  .viewport .listbox-picker-option,
  .viewport :global(.picker-rail-row) {
    transition: background-color var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease);
  }
  @keyframes picker-panel-in {
    from { opacity: 0; transform: translateY(var(--picker-shift, 6px)) scale(0.98); }
    to { opacity: 1; transform: none; }
  }
  @keyframes picker-panel-out {
    from { opacity: 1; transform: none; }
    to { opacity: 0; transform: translateY(var(--picker-shift, 6px)) scale(0.98); }
  }
  @keyframes picker-list-in {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .listbox-picker-panel.viewport.placed,
    .listbox-picker-panel.viewport.closing,
    .listbox-picker-list-motion,
    .viewport .listbox-picker-option,
    .viewport :global(.picker-rail-row) { animation: none; transition: none; }
  }
  .listbox-picker-panel[aria-busy="true"] .listbox-picker-option { cursor: progress; opacity: .55; }
  @media (max-width: 620px) {
    .listbox-picker-panel.rail { width: min(274px, calc(100vw - var(--space-4) * 2)); }
  }
</style>
