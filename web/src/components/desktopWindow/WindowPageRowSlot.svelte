<script lang="ts">
  /** The page row on the desktop window's top row: whatever row the open page lifted
   *  (WindowPageRow), pinned on the window-control line in the reading column's edges, with
   *  the always-on fade under the top row. Rendered only in the desktop window.
   *
   *  A narrow row shortens its context line first (one line, an ellipsis, down to a floor), then
   *  folds its actions, last first, into a "•••" menu at the row's end. A page marks the parts:
   *  `data-window-row-context` on its context line, `data-window-row-actions` on the element
   *  that holds its actions. Each button, link or disclosure in there is one action. */
  import { tick } from "svelte";
  import { floatingPanel } from "../../lib/floatingPanel";
  import { foldedActionCount } from "../../lib/desktopWindow/pageRowFold";
  import { PAGE_ROW_CONTEXT_MIN_WIDTH_PX, PAGE_ROW_GAP_PX } from "../../lib/desktopWindow/config";
  import { shownPageRow } from "../../lib/desktopWindow/windowState.svelte";

  const FOLDED = "data-window-row-folded";
  const ACTION = ":is(button, a[href], summary)";

  let row = $state<HTMLElement | null>(null);
  let content = $state<HTMLElement | null>(null);
  let moreButton = $state<HTMLButtonElement | null>(null);
  let menu = $state<HTMLElement | null>(null);
  let folded = $state.raw<HTMLElement[]>([]);
  let menuOpen = $state(false);
  const shown = $derived(shownPageRow());

  function actions(root: HTMLElement): HTMLElement[] {
    const found = Array.from(root.querySelectorAll<HTMLElement>(`[data-window-row-actions] ${ACTION}`))
      .filter(element => !element.closest("dialog, [data-window-row-context]") && element.getClientRects().length > 0);
    return found.filter(element => !found.some(other => other !== element && other.contains(element)));
  }

  /** The width of what stands before the actions when the page marks no context line: the
   *  lifted root's children up to the one that holds the first action. */
  function leadingWidth(root: HTMLElement, first: HTMLElement): number {
    const lifted = root.firstElementChild;
    if (!lifted) return 0;
    let total = 0;
    for (const child of Array.from(lifted.children)) {
      if (child.contains(first)) break;
      total += child.getBoundingClientRect().width;
    }
    return total;
  }

  function label(control: HTMLElement): string {
    return control.getAttribute("aria-label") || control.textContent?.trim() || "Action";
  }

  function fold(): void {
    if (!content || !row || !moreButton) return;
    for (const element of content.querySelectorAll(`[${FOLDED}]`)) element.removeAttribute(FOLDED);
    const units = actions(content);
    if (units.length === 0) { if (folded.length) folded = []; menuOpen = false; return; }
    const context = content.querySelector<HTMLElement>("[data-window-row-context]");
    const edges = units.map(unit => unit.getBoundingClientRect());
    const widths = edges.map((edge, index) => index === 0 ? edge.width : edge.right - edges[index - 1].right);
    const count = foldedActionCount({
      rowWidth: row.clientWidth,
      contextWidth: context ? context.scrollWidth : leadingWidth(content, units[0]),
      contextMinWidth: context ? PAGE_ROW_CONTEXT_MIN_WIDTH_PX : Number.POSITIVE_INFINITY,
      actionWidths: widths,
      actionGap: 0,
      rowGap: context ? PAGE_ROW_GAP_PX : 0,
      moreWidth: moreButton.getBoundingClientRect().width + 2
    });
    const next = count ? units.slice(units.length - count) : [];
    for (const unit of next) unit.setAttribute(FOLDED, "");
    if (next.length !== folded.length || next.some((unit, index) => unit !== folded[index])) {
      folded = next;
      menuOpen = false;
    }
  }

  // Refold when the row's width changes and when the page changes what is in it (a busy
  // label, an action that comes or goes). The fold marks are attributes, which the observer
  // does not watch, so folding never wakes it.
  $effect(() => {
    if (!row || !content) return;
    void shown;
    let frame = 0;
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fold); };
    const sizes = new ResizeObserver(schedule);
    sizes.observe(row);
    const changes = new MutationObserver(schedule);
    changes.observe(content, { childList: true, subtree: true, characterData: true });
    void tick().then(fold);
    return () => { cancelAnimationFrame(frame); sizes.disconnect(); changes.disconnect(); };
  });

  function closeMenu(focusButton = false): void {
    menuOpen = false;
    if (focusButton) moreButton?.focus();
  }

  async function toggleMenu(): Promise<void> {
    menuOpen = !menuOpen;
    if (!menuOpen) return;
    await tick();
    menu?.querySelector<HTMLElement>("[role='menuitem']:not(:disabled)")?.focus();
  }

  function choose(control: HTMLElement): void {
    closeMenu();
    control.click();
  }

  function menuKey(event: KeyboardEvent): void {
    if (event.key === "Escape") { event.preventDefault(); closeMenu(true); return; }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(menu?.querySelectorAll<HTMLElement>("[role='menuitem']:not(:disabled)") ?? []);
    const at = items.indexOf(document.activeElement as HTMLElement);
    items[(at + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
  }

  function outside(event: PointerEvent): void {
    if (!menuOpen) return;
    const target = event.target as Node;
    if (!menu?.contains(target) && !moreButton?.contains(target)) closeMenu();
  }

  function disabled(control: HTMLElement): boolean {
    return control.matches(":disabled") || control.getAttribute("aria-disabled") === "true";
  }
</script>

<svelte:window onpointerdown={outside} />

<div class="window-page-row-slot" data-window-page-row-slot>
  <div class="window-page-row" bind:this={row} data-window-page-row>
    <div class="window-page-row-content" bind:this={content}>{#if shown}{@render shown()}{/if}</div>
    <button bind:this={moreButton} type="button" class="button button--ghost window-page-row-more" class:window-page-row-more--shown={folded.length > 0}
      aria-label="More actions" aria-haspopup="menu" aria-expanded={menuOpen} tabindex={folded.length > 0 ? 0 : -1} aria-hidden={folded.length > 0 ? undefined : "true"}
      onclick={() => void toggleMenu()}>•••</button>
    {#if menuOpen && folded.length > 0}
      <div class="window-page-row-menu" role="menu" aria-label="More actions" tabindex="-1" bind:this={menu} onkeydown={menuKey} popover="manual" {@attach floatingPanel(moreButton, "bottom-end")}>
        {#each folded as control}
          <button type="button" role="menuitem" class="window-page-row-menu-item" disabled={disabled(control)} onclick={() => choose(control)}>{label(control)}</button>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  /* Over the pane's top, from the window's top edge to the end of the fade under the top row.
     It takes no pointer itself, so the empty row stays the window's drag region. Its sides
     are the reading column's (the column insets that keep it clear of the sidebar button). */
  .window-page-row-slot { position:absolute; z-index:1; top:0; left:0; right:0; height:calc(var(--window-top-row) + var(--window-scroll-fade)); padding-inline:var(--window-column-inset-start, 0px) var(--window-column-inset-end, 0px); pointer-events:none; transition:padding-left var(--motion-slow) var(--motion-ease), padding-right var(--motion-slow) var(--motion-ease); }
  .window-page-row-slot::after { content:""; position:absolute; left:0; right:0; top:var(--window-top-row); height:var(--window-scroll-fade); background:linear-gradient(to bottom, var(--surface-1), transparent); }
  /* Centred on the window-control line (y 26): top 12, the page controls' 28px. */
  .window-page-row { position:relative; z-index:1; display:flex; align-items:center; gap:2px; width:min(var(--page-measure), calc(100% - 2 * var(--space-5))); height:var(--page-control-height); margin:calc((var(--window-top-row) - var(--page-control-height)) / 2) auto 0; }
  .window-page-row { pointer-events:auto; }
  .window-page-row-more, .window-page-row-menu { -webkit-app-region:no-drag; }
  .window-page-row-content { flex:1 1 auto; min-width:0; height:100%; display:flex; align-items:center; }
  .window-page-row-content :global(:is(button, a, input, select, summary, label, [tabindex])) { -webkit-app-region:no-drag; }
  .window-page-row-more { display:none; flex:none; padding:0 var(--space-2); letter-spacing:1px; }
  .window-page-row-more--shown { display:inline-flex; }
  /* Measured while hidden: in the box, out of sight and out of the way. */
  .window-page-row-more:not(.window-page-row-more--shown) { display:inline-flex; position:absolute; right:0; visibility:hidden; }
  .window-page-row-menu { min-width:200px; padding:var(--space-1); border-radius:var(--radius-md); background:var(--surface-raised); box-shadow:0 0 0 1px var(--border-raised), var(--shadow-float); }
  .window-page-row-menu-item { display:block; width:100%; padding:6px 10px; border:0; border-radius:6px; background:transparent; color:var(--tone-primary); font:inherit; font-size:var(--type-row); text-align:left; cursor:pointer; white-space:nowrap; }
  .window-page-row-menu-item:hover:not(:disabled), .window-page-row-menu-item:focus-visible { background:var(--surface-hover); }
  .window-page-row-menu-item:disabled { color:var(--tone-faint); cursor:default; }

  /* The lifted rows, laid on one 28px line: the context line one line with an ellipsis, the
     actions never wrapping (they fold instead), and a page's notes hanging under its actions.
     The slot's two classes lead every rule, so these win over each page's own band rules. */
  .window-page-row-slot .window-page-row-content > :global(*) { flex:1 1 auto; min-width:0; min-height:0; height:100%; margin-block:0; padding-block:0; display:flex; align-items:center; flex-wrap:nowrap; }
  .window-page-row-slot .window-page-row-content :global([data-window-row-context]) { display:block; flex:1 1 auto; min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; line-height:var(--page-control-height); }
  .window-page-row-slot .window-page-row-content :global([data-window-row-context] .page-eyebrow) { display:block; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; line-height:var(--page-control-height); }
  .window-page-row-slot .window-page-row-content :global(:is([data-window-row-context], [data-window-row-context] .page-eyebrow) > *) { vertical-align:middle; }
  .window-page-row-slot .window-page-row-content :global([data-window-row-actions]) { flex:none; flex-wrap:nowrap; margin-right:0; padding-block:0; }
  .window-page-row-slot .window-page-row-content :global([data-window-row-actions] > *) { flex:none; }
  .window-page-row-slot .window-page-row-content :global([data-window-row-folded]) { display:none; }
  /* Every control on the row is the page controls' 28px, so all of them centre on the line. */
  .window-page-row-slot .window-page-row :global(.button) { height:var(--page-control-height); }
  .window-page-row-slot .window-page-row-content :global(.ticket-lifecycle) { flex:none; height:100%; justify-content:flex-start; margin-right:0; }
  .window-page-row-slot .window-page-row-content :global(.ticket-lifecycle .hint) { margin:var(--space-1) 0 0; white-space:nowrap; }
  .window-page-row-slot .window-page-row-content :global(.goal-context > *) { flex-shrink:0; }

  /* An inactive window dims its page row. */
  :global(:root[data-window-inactive]) .window-page-row :global(*:not(dialog, dialog *)) { color:var(--tone-faint); }

  @media (prefers-reduced-motion: reduce) { .window-page-row-slot { transition:none; } }
</style>
