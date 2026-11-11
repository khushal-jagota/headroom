<script lang="ts">
  import { getContext } from "svelte";
  import { fade } from "svelte/transition";
  import type { Block, TableBlock } from "../../lib/pageKit";

  import { PAGE_CONTEXT, type PageContext } from "../../lib/pageKit/context";
  import PageText from "./PageText.svelte";
  import { TABLE_COLUMN_MIN_WIDTH, TABLE_COLUMN_KEY_STEP, TABLE_COLUMN_LARGE_KEY_STEP } from "../../lib/pageKit/tableSizing";
  const context = getContext<PageContext | undefined>(PAGE_CONTEXT);

  let { block, selectable = false }: { block: TableBlock; selectable?: boolean } = $props();

  type Direction = "asc" | "desc";
  type Line = { key: number; cells: string[] };

  const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
  const glideMs = 680;

  let sort = $state<{ index: number; direction: Direction } | null>(null);
  let selected = $state<number[]>([]);
  let message = $state("");
  let reduced = $state(false);
  let shiftHeld = false;
  let anchor: number | null = null;
  let tableEl = $state<HTMLTableElement | null>(null);
  // Widths measured for one column count. A block that gains or loses columns
  // lays out again and is measured again, so new columns never get zero width.
  let measured = $state<{ count: number; widths: number[]; minWidth: number } | null>(null);
  let measuredWidths = $derived(measured?.count === block.columns.length ? measured.widths : null);
  let preferenceKey = $derived(JSON.stringify(["page-table-widths", context?.goalId ?? window.location.pathname + window.location.hash, block.id, block.columns]));
  let resized = $state<{ key: string; widths: number[] } | null>(null);
  let pixelWidths = $derived(resized?.key === preferenceKey ? resized.widths : null);
  let widths = $derived(pixelWidths ?? block.column_widths ?? measuredWidths);
  let drag = $state<{ pointerId: number; index: number; x: number; widths: number[] } | null>(null);

  $effect(() => {
    const key = preferenceKey;
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(key) ?? "null");
      if (Array.isArray(saved) && saved.length === block.columns.length && saved.every(width => typeof width === "number" && Number.isFinite(width) && width > 0)) {
        resized = { key, widths: saved };
      }
    } catch { /* Browser storage can be unavailable. Resizing still works for this visit. */ }
  });

  function currentWidths(): number[] {
    return [...(tableEl?.querySelectorAll<HTMLTableCellElement>("thead th[data-key]") ?? [])]
      .map(cell => cell.getBoundingClientRect().width);
  }

  function resizeColumn(index: number, width: number, initial = pixelWidths ?? currentWidths()) {
    resized = { key: preferenceKey, widths: initial.map((value, column) => column === index ? Math.max(TABLE_COLUMN_MIN_WIDTH, width) : value) };
  }

  function columnWidth(index: number): number {
    return Math.round(pixelWidths?.[index] ?? tableEl?.querySelectorAll<HTMLTableCellElement>("thead th[data-key]")[index]?.getBoundingClientRect().width ?? TABLE_COLUMN_MIN_WIDTH);
  }

  function rememberWidths() {
    if (!pixelWidths) return;
    try { localStorage.setItem(preferenceKey, JSON.stringify(pixelWidths)); }
    catch { /* A local preference must not prevent a resize. */ }
  }

  function startResize(event: PointerEvent, index: number) {
    if (!event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    const handle = event.currentTarget as HTMLElement;
    handle.focus({ preventScroll: true });
    handle.setPointerCapture(event.pointerId);
    drag = { pointerId: event.pointerId, index, x: event.clientX, widths: currentWidths() };
  }

  function moveResize(event: PointerEvent) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    resizeColumn(drag.index, drag.widths[drag.index] + event.clientX - drag.x, drag.widths);
  }

  function finishResize(event: PointerEvent) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    rememberWidths();
    drag = null;
  }

  function resizeKey(event: KeyboardEvent, index: number) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.stopPropagation();
    const initial = pixelWidths ?? currentWidths();
    const step = event.shiftKey ? TABLE_COLUMN_LARGE_KEY_STEP : TABLE_COLUMN_KEY_STEP;
    resizeColumn(index, initial[index] + (event.key === "ArrowRight" ? step : -step), initial);
    rememberWidths();
    message = `${block.columns[index]} width ${Math.round(pixelWidths?.[index] ?? 0)} pixels`;
  }
  let band = $state<{ left: number; width: number } | null>(null);
  let scrolled = $state(false);

  $effect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduced = media.matches;
    const sync = () => {
      reduced = media.matches;
    };
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  });

  $effect(() => {
    const table = tableEl;
    if (!table || widths) return;
    const measure = () => {
      if (getComputedStyle(table).display !== "table") return false;
      const total = table.getBoundingClientRect().width;
      const cells = [...table.querySelectorAll<HTMLTableCellElement>("thead th[data-key]")];
      if (!total || cells.length !== block.columns.length) return false;
      const percents = cells.map((cell) => (cell.getBoundingClientRect().width / total) * 100);
      // The narrowest table width at which every column still holds its content
      // at its measured share. The fixed layout keeps it, so a crowded table scrolls.
      table.style.width = "0";
      const minWidth = Math.max(
        table.getBoundingClientRect().width,
        ...cells.map((cell, index) => cell.getBoundingClientRect().width / (percents[index] / 100)),
      );
      table.style.width = "";
      measured = { count: cells.length, widths: percents, minWidth };
      return true;
    };
    if (measure()) return;
    const observer = new ResizeObserver(() => {
      if (measure()) observer.disconnect();
    });
    observer.observe(table);
    return () => observer.disconnect();
  });

  $effect(() => {
    const table = tableEl;
    const index = sort?.index;
    void widths;
    if (!table) return;
    let current: { left: number; width: number } | null = null;
    const update = () => {
      const cells = [...table.querySelectorAll<HTMLTableCellElement>("thead th[data-key]")];
      const cell = index === undefined ? undefined : cells[index];
      if (!cell) {
        if (current) {
          current = null;
          band = null;
        }
        return;
      }
      const next = { left: cell.offsetLeft, width: cell.offsetWidth };
      if (current && current.left === next.left && current.width === next.width) return;
      current = next;
      band = next;
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(table);
    return () => observer.disconnect();
  });

  function asNumber(cell: string): number | null {
    const trimmed = cell.trim().replace(/[$,]/g, "").replace(/%$/, "");
    if (!/^-?\d+(\.\d+)?$/.test(trimmed)) return null;
    return Number(trimmed);
  }

  function missing(cell: string): boolean {
    return ["", "—", "–"].includes(cell.trim());
  }

  function numericColumn(index: number): boolean {
    const filled = block.rows.map((row) => row[index] ?? "").filter((cell) => !missing(cell));
    return filled.length > 0 && filled.every((cell) => asNumber(cell) !== null);
  }

  let numeric = $derived(block.columns.map((_, index) => numericColumn(index)));
  /** A column where any cell carries a status keeps a dot slot in every cell, so its values
   *  share one edge and one line whether or not they carry a dot. */
  let toned = $derived(block.columns.map((_, index) => (block.cell_tones ?? []).some((row) => Boolean(row?.[index]))));

  let ordered = $derived.by(() => {
    const lines: Line[] = block.rows.map((cells, key) => ({ key, cells }));
    const active = sort;
    if (!active || active.index >= block.columns.length) return lines;
    const index = active.index;
    const numbers = numeric[index];
    return lines.slice().sort((a, b) => {
      const left = a.cells[index] ?? "";
      const right = b.cells[index] ?? "";
      const leftEmpty = missing(left);
      const rightEmpty = missing(right);
      if (leftEmpty || rightEmpty) {
        if (leftEmpty === rightEmpty) return a.key - b.key;
        return leftEmpty ? 1 : -1;
      }
      const result = numbers
        ? (asNumber(left) ?? 0) - (asNumber(right) ?? 0)
        : collator.compare(left, right);
      return (active.direction === "desc" ? -result : result) || a.key - b.key;
    });
  });

  let selectedSet = $derived(new Set(selected));
  let selectedCount = $derived(ordered.filter((line) => selectedSet.has(line.key)).length);
  let allSelected = $derived(ordered.length > 0 && selectedCount === ordered.length);

  let countLabel = $derived.by(() => {
    if (selectable && selectedCount > 0) return `${selectedCount} selected`;
    const count = block.rows.length;
    const name = (block.columns[0] ?? "row").trim().toLowerCase() || "row";
    const noun = count === 1 || name.endsWith("s") ? name : `${name}s`;
    return `${count} ${noun}`;
  });

  function commit(next: Set<number>) {
    const order = ordered.map((line) => line.key);
    selected = order.filter((key) => next.has(key));
    message = selected.length ? `${selected.length} of ${order.length} selected` : "Selection cleared";
  }

  function toggleRow(key: number, extend: boolean) {
    const next = new Set(selected);
    const turningOn = !next.has(key);
    const order = ordered.map((line) => line.key);
    const from = extend && anchor !== null ? order.indexOf(anchor) : -1;
    const to = order.indexOf(key);
    const span = from < 0 ? [key] : order.slice(Math.min(from, to), Math.max(from, to) + 1);
    for (const item of span) {
      if (turningOn) next.add(item);
      else next.delete(item);
    }
    anchor = key;
    shiftHeld = false;
    commit(next);
  }

  function toggleAll() {
    anchor = null;
    if (allSelected) {
      selected = [];
      message = "Selection cleared";
      return;
    }
    selected = ordered.map((line) => line.key);
    message = `${selected.length} of ${ordered.length} selected`;
  }

  function clearSelection() {
    anchor = null;
    selected = [];
    message = "Selection cleared";
    tableEl?.querySelector<HTMLInputElement>("[data-role=all]")?.focus();
  }

  function columnText(index: number, raw: string, old: Block): Block {
    if (old.type !== "table" || index >= old.columns.length) throw new Error("This table column no longer exists.");
    return { ...old, columns: old.columns.map((column, sourceIndex) => sourceIndex === index ? raw : column) };
  }

  function cellText(row: number, column: number, raw: string, old: Block): Block {
    if (old.type !== "table" || !old.rows[row] || column >= old.rows[row].length) throw new Error("This table cell no longer exists.");
    return { ...old, rows: old.rows.map((cells, sourceRow) => sourceRow === row ? cells.map((cell, sourceColumn) => sourceColumn === column ? raw : cell) : cells) };
  }

  function onRowClick(event: MouseEvent, key: number) {
    if (!selectable) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("a, button, input, label, select, textarea, [role=button], [role=textbox], [contenteditable=true]")) return;
    if (window.getSelection()?.toString()) return;
    toggleRow(key, event.shiftKey);
    (event.currentTarget as HTMLElement).querySelector<HTMLInputElement>("input[type=checkbox]")?.focus({ preventScroll: true });
  }

  function onRowDown(event: MouseEvent) {
    shiftHeld = event.shiftKey;
    if (!selectable || !event.shiftKey) return;
    const target = event.target;
    if (target instanceof Element && target.closest("a, button, input, label, select, textarea, [role=button], [role=textbox], [contenteditable=true]")) return;
    event.preventDefault();
  }

  function indeterminate(node: HTMLInputElement, mixed: boolean) {
    node.indeterminate = mixed;
    return {
      update(next: boolean) {
        node.indeterminate = next;
      }
    };
  }

  function ariaSort(index: number): "none" | "ascending" | "descending" {
    if (sort?.index !== index) return "none";
    return sort.direction === "asc" ? "ascending" : "descending";
  }

  function sortLabel(index: number): string {
    const name = block.columns[index];
    const state = ariaSort(index);
    if (state === "none") return `Sort by ${name}`;
    return `Sort by ${name}, currently ${state}`;
  }

  function sortBy(index: number) {
    const direction: Direction = sort?.index === index && sort.direction === "asc" ? "desc" : "asc";
    sort = { index, direction };
    message = `Sorted by ${block.columns[index]}, ${direction === "asc" ? "ascending" : "descending"}`;
  }

  function keys(node: HTMLElement): { destroy: () => void } {
    const onKeydown = (event: KeyboardEvent) => moveFocus(node, event);
    node.addEventListener("keydown", onKeydown);
    return { destroy: () => node.removeEventListener("keydown", onKeydown) };
  }

  function moveFocus(node: HTMLElement, event: KeyboardEvent) {
    if (event.target instanceof Element && event.target.closest("[contenteditable=true], textarea, [role=textbox]")) return;
    if (event.key === " " || event.key === "Enter") shiftHeld = event.shiftKey;
    if (event.key === "Escape" && selectable && selectedCount > 0) {
      event.preventDefault();
      clearSelection();
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.dataset.nav) return;
    const nav = target.dataset.nav;
    const list = (name: string) => [...node.querySelectorAll<HTMLElement>(`[data-nav="${name}"]`)].filter((item) => item.getClientRects().length > 0);
    const items = list(nav);
    const index = items.indexOf(target);
    if (index < 0) return;
    const selectAll = node.querySelector<HTMLElement>("[data-role=all]");
    let next: HTMLElement | undefined;
    if (nav === "head") {
      if (event.key === "ArrowRight") next = items[index + 1];
      else if (event.key === "ArrowLeft") next = items[index - 1];
      else if (event.key === "Home") next = items[0];
      else if (event.key === "End") next = items[items.length - 1];
      else if (event.key === "ArrowDown" && target.dataset.role === "all") next = list("row")[0];
    } else if (event.key === "ArrowDown") next = items[index + 1];
    else if (event.key === "ArrowUp") next = index === 0 ? selectAll ?? undefined : items[index - 1];
    else if (event.key === "Home") next = items[0];
    else if (event.key === "End") next = items[items.length - 1];
    if (!next || next === target) return;
    event.preventDefault();
    next.focus();
  }

  /** Underdamped spring from `from` toward 0, with a short settle and a small overshoot. */
  function springOffset(from: number, elapsedMs: number): number {
    const stiffness = 64;
    const damping = 14;
    const t = elapsedMs / 1000;
    const decay = Math.exp((-damping / 2) * t);
    const w = Math.sqrt(Math.max(0.001, stiffness - (damping * damping) / 4));
    const bounce = (damping / 2) / w;
    return decay * (from * Math.cos(w * t) + from * bounce * Math.sin(w * t));
  }

  function rowGlide(node: HTMLTableRowElement, { from, to }: { from: DOMRect; to: DOMRect }) {
    const dy = from.top - to.top;
    if (!dy || reduced) return { duration: 0 };
    const cells = [...node.cells];
    const started = performance.now();
    for (const cell of cells) cell.style.zIndex = "2";
    return {
      duration: glideMs,
      easing: (t: number) => t,
      tick: (t: number) => {
        const y = t >= 1 ? 0 : springOffset(dy, performance.now() - started);
        const moving = Math.abs(y) > 0.4;
        for (const cell of cells) {
          cell.style.transform = moving ? `translateY(${y}px)` : "";
          if (!moving) cell.style.zIndex = "";
        }
      }
    };
  }
</script>

<!-- A number column shows its sort button before the label on wide pages and after it
     on phones. Each layout shows one, so Tab order follows what the reader sees. -->
{#snippet headSort(index: number, placement: "before" | "after")}
  <button type="button" class="sort icon-only" data-placement={placement} data-nav="head" aria-label={sortLabel(index)} onclick={() => sortBy(index)}>
    <span class="sort-icon" aria-hidden="true">
      <svg class="sort-hint" viewBox="0 0 16 16"><path d="M8 12.25V3.75M4.75 7 8 3.75 11.25 7" /></svg>
      <svg class="sort-arrow" viewBox="0 0 16 16"><path d="M8 12.25V3.75M4.75 7 8 3.75 11.25 7" /></svg>
    </span>
  </button>
{/snippet}

{#snippet box(checked: boolean, mixed: boolean, label: string, nav: "head" | "row", role: string, onToggle: (extend: boolean) => void)}
  <label class="hit">
    <input
      type="checkbox"
      class="hit-input"
      {checked}
      aria-label={label}
      data-nav={nav}
      data-role={role || undefined}
      use:indeterminate={mixed}
      onpointerdown={(event) => { shiftHeld = event.shiftKey; }}
      onchange={() => onToggle(shiftHeld)}
    />
    <span class="box" data-on={checked || mixed ? "" : undefined} data-mixed={mixed ? "" : undefined} aria-hidden="true">
      <span class="fill"></span>
      <svg class="glyph" viewBox="0 0 18 18">
        <path class="check" pathLength="1" d="M4.25 9.25 L7.25 12.25 L13.75 5.75" />
        <path class="dash" pathLength="1" d="M4.75 9 L9 9 L13.25 9" />
      </svg>
    </span>
  </label>
{/snippet}

<div class="table-block" class:reduced data-block="table" data-selectable={selectable ? "" : undefined} use:keys>
  {#if block.caption}<div class="caption"><PageText blockId={block.id} field="caption" value={block.caption} label="Table caption" update={(old, raw) => {
    if (old.type !== "table") throw new Error("This table no longer exists.");
    return { ...old, caption: raw };
  }} /></div>{/if}
  <div class="card">
    <div class="scroller" data-scrolled={scrolled ? "" : undefined} onscroll={(event) => (scrolled = event.currentTarget.scrollLeft > 0)}>
      {#if band}<span class="band" style:left="{band.left}px" style:width="{band.width}px" aria-hidden="true"></span>{/if}
      <table bind:this={tableEl} data-fixed={widths ? "" : undefined} data-resized={pixelWidths ? "" : undefined} style:width={pixelWidths ? `calc(${pixelWidths.reduce((sum, width) => sum + width, 0)}px + ${selectable ? "var(--space-7)" : "0px"})` : undefined} style:min-width={pixelWidths ? "0" : undefined} style:--table-min-width={!pixelWidths && !block.column_widths && measuredWidths ? `${measured?.minWidth}px` : undefined} data-selectable={selectable ? "" : undefined}>
        {#if block.caption}<caption class="sr">{block.caption}</caption>{/if}
        {#if widths || selectable}
          <colgroup>
            {#if selectable}<col class="select-col" />{/if}
            {#if widths}
              {#each widths as width, index (`col-${index}`)}
                <col style:width={pixelWidths ? `${width}px` : `${width}%`} />
              {/each}
            {/if}
          </colgroup>
        {/if}
        <thead>
          <tr>
            {#if selectable}
              <th class="select-cell" scope="col">
                {@render box(allSelected, selectedCount > 0 && !allSelected, "Select all rows", "head", "all", () => toggleAll())}
              </th>
            {/if}
            {#each block.columns as column, index (index)}
              <th
                scope="col"
                data-key={index}
                aria-sort={ariaSort(index)}
                data-sorted={sort?.index === index ? "" : undefined}
                data-numeric={numeric[index] ? "" : undefined}
              >
                {#if context?.authorText}
                  <div class="editable-sort">
                    {#if numeric[index]}{@render headSort(index, "before")}{/if}
                    <div class="header-copy"><PageText blockId={block.id} field={`columns.${index}`} value={column} label={`Table column ${index + 1}`} update={(old, raw) => columnText(index, raw, old)} /></div>
                    {@render headSort(index, "after")}
                  </div>
                {:else}
                  <button type="button" class="sort" data-nav="head" aria-label={sortLabel(index)} onclick={() => sortBy(index)}>
                    <span class="sort-inner">
                      <span class="header-copy">{column}</span>
                      <span class="sort-icon" aria-hidden="true">
                        <svg class="sort-hint" viewBox="0 0 16 16"><path d="M8 12.25V3.75M4.75 7 8 3.75 11.25 7" /></svg>
                        <svg class="sort-arrow" viewBox="0 0 16 16"><path d="M8 12.25V3.75M4.75 7 8 3.75 11.25 7" /></svg>
                      </span>
                    </span>
                  </button>
                {/if}
                <!-- A focusable separator follows the WAI window splitter pattern: arrow keys resize the column. -->
                <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
                <div
                  class="resize-handle"
                  class:resizing={drag?.index === index}
                  role="separator"
                  tabindex="0"
                  aria-label={`Resize ${column} column`}
                  aria-orientation="vertical"
                  aria-valuemin={Math.min(TABLE_COLUMN_MIN_WIDTH, columnWidth(index))}
                  aria-valuenow={columnWidth(index)}
                  aria-valuemax={Number.MAX_SAFE_INTEGER}
                  aria-valuetext={`${columnWidth(index)} pixels`}
                  onpointerdown={(event) => startResize(event, index)}
                  onpointermove={moveResize}
                  onpointerup={finishResize}
                  onpointercancel={finishResize}
                  onlostpointercapture={finishResize}
                  onkeydown={(event) => resizeKey(event, index)}
                ></div>
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#if ordered.length === 0}
            <tr>
              <td class="empty" colspan={block.columns.length + (selectable ? 1 : 0)}>
                {#if block.empty_message !== undefined}<PageText blockId={block.id} field="empty_message" value={block.empty_message} label="Table empty message" update={(old, raw) => {
                  if (old.type !== "table") throw new Error("This table no longer exists.");
                  return { ...old, empty_message: raw };
                }} />{:else}No rows to show{/if}
              </td>
            </tr>
          {:else}
            {#each ordered as line (line.key)}
              <tr
                animate:rowGlide
                data-selected={selectable && selectedSet.has(line.key) ? "" : undefined}
                onmousedown={onRowDown}
                onclick={(event) => onRowClick(event, line.key)}
              >
                {#if selectable}
                  <td class="select-cell">
                    {@render box(selectedSet.has(line.key), false, `Select ${line.cells[0] || "row"}`, "row", "", (extend) => toggleRow(line.key, extend))}
                  </td>
                {/if}
                {#each line.cells as cell, index (index)}
                  <td
                    data-primary={index === 0 ? "" : undefined}
                    data-sorted={sort?.index === index ? "" : undefined}
                    data-numeric={numeric[index] ? "" : undefined}
                    data-tone={block.cell_tones?.[line.key]?.[index] ?? undefined}
                    data-missing={missing(cell) ? "" : undefined}
                    data-dot-slot={toned[index] ? "" : undefined}
                  ><div class="cell-copy">{#if toned[index]}<span class="cell-status-dot" aria-hidden="true"></span>{/if}<PageText blockId={block.id} field={`rows.${line.key}.${index}`} value={cell} label={`${block.columns[index] ?? `Column ${index + 1}`}, row ${line.key + 1}`} update={(old, raw) => cellText(line.key, index, raw, old)} /></div></td>
                {/each}
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>
    {#if selectable}
    <div class="footer">
      <p class="count" data-active={selectable && selectedCount > 0 ? "" : undefined}>{countLabel}</p>
      {#if selectable && selectedCount > 0}
        <button type="button" class="clear" data-clear transition:fade={{ duration: reduced ? 0 : 140 }} onclick={clearSelection}>Clear</button>
      {/if}
    </div>
    {/if}
  </div>
  <p class="sr" role="status">{message}</p>
</div>

<style>
  /* THE CARD (page system). The table sits on the card itself: no second box inside it,
     only the hairlines between its rows. Its first and last columns stand on the card's
     inset, the same edge as the caption above them. */
  .table-block {
    min-width: 0;
    padding: var(--page-card-pad);
    border: var(--border-hairline) solid var(--page-card-edge);
    border-radius: var(--page-card-radius);
    background: var(--page-card-fill);
    color: var(--tone-primary);
  }
  .caption {
    margin: 0 0 var(--page-head-gap);
    color: var(--tone-primary);
    font-size: var(--type-page-section);
    font-weight: var(--weight-page-section);
    line-height: 20px;
    letter-spacing: 0;
  }
  .card {
    --column-wash: var(--wash-context);
    --row-line: var(--border-color);
    position: relative;
    min-width: 0;
    overflow: hidden;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--tone-primary);
  }
  .scroller { position: relative; overflow-x: auto; overscroll-behavior-x: contain; }
  .band {
    position: absolute;
    top: 0;
    bottom: 0;
    z-index: 0;
    background: var(--column-wash);
    pointer-events: none;
  }
  table {
    position: relative;
    z-index: 1;
    width: 100%;
    min-width: max(560px, var(--table-min-width, 0px));
    border-collapse: separate;
    border-spacing: 0;
    font-size: var(--type-body);
    line-height: 20px;
    text-align: left;
  }
  table[data-fixed] { table-layout: fixed; }
  th, td {
    --cell-base: transparent;
    --cell-fill: var(--cell-base);
    position: relative;
    background-color: var(--cell-fill);
    vertical-align: middle;
  }
  th[data-sorted], td[data-sorted] { --cell-base: var(--column-wash); }
  th {
    height: 32px;
    padding: 0;
    border-bottom: var(--border-hairline) solid var(--row-line);
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
    white-space: nowrap;
  }
  th[data-sorted] { color: var(--tone-secondary); }
  td {
    height: 40px;
    padding: 0 var(--space-4);
    border-bottom: var(--border-hairline) solid var(--row-line);
    color: var(--tone-primary);
    font-weight: var(--weight-body);
    font-variant-numeric: tabular-nums;
    overflow-wrap: anywhere;
  }
  /* A label cell keeps room to read when columns are added, and breaks long words only where
     it must (staging); its words in the page's voice. */
  td[data-primary] { min-width: 180px; color: var(--tone-primary); font-weight: var(--weight-body); overflow-wrap: break-word; }
  td[data-missing] { color: var(--tone-tertiary); }
  /* As staging, a cell with a status says it in its colour: the value and its dot in the
     status colour. In a column with any status every cell keeps the dot's slot (empty where
     there is none), so the values share one right edge and one line. */
  td[data-tone="good"] { color: var(--accent-done); }
  td[data-tone="attention"] { color: var(--accent-attention); }
  td[data-tone="bad"] { color: var(--accent-error); }
  td[data-tone="neutral"] { color: var(--chart-legend); }
  td[data-tone="info"] { color: var(--accent-bright); }
  td[data-dot-slot] .cell-copy, td[data-numeric] .cell-copy { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
  td[data-numeric] .cell-copy { justify-content: flex-end; }
  .cell-status-dot { flex: none; width: 5px; height: 5px; border-radius: 50%; background: transparent; }
  td[data-tone] .cell-status-dot { background: currentColor; }
  tbody tr:last-child td { border-bottom: 0; }
  /* The outer columns stand on the card's inset, so the rules between rows span exactly the
     card's content width. */
  td:first-child, th:first-child .sort, th:first-child .editable-sort { padding-left: 0; }
  td:last-child, th:last-child .sort { padding-right: 0; }
  table[data-selectable] tbody tr { cursor: pointer; }
  tbody tr[data-selected] td {
    --cell-fill: color-mix(in srgb, var(--accent-bright) 8%, var(--cell-base));
  }
  .select-col { width: var(--space-7); }
  .select-cell { width: var(--space-7); padding: 0 0 0 var(--space-3); }
  .hit {
    position: relative;
    display: flex;
    width: 100%;
    height: 44px;
    align-items: center;
    cursor: pointer;
  }
  td.select-cell .hit { height: 40px; }
  .hit-input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    opacity: 0;
    cursor: pointer;
  }
  .box {
    position: relative;
    display: block;
    width: var(--space-4);
    height: var(--space-4);
    flex: none;
    border: var(--border-hairline) solid var(--chart-grid);
    border-radius: var(--radius-sm);
    background: var(--chart-surface);
    pointer-events: none;
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .box[data-on] { border-color: var(--text-strong); }
  .fill {
    position: absolute;
    inset: -1px;
    border-radius: inherit;
    background: var(--text-strong);
    opacity: 0;
    transform: scale(0.6);
    transition: opacity var(--motion-fast) linear, transform 180ms cubic-bezier(0.16, 1.4, 0.3, 1);
  }
  .box[data-on] .fill { opacity: 1; transform: scale(1); }
  .glyph {
    position: absolute;
    inset: -1px;
    width: var(--space-4);
    height: var(--space-4);
    overflow: visible;
  }
  .glyph path {
    fill: none;
    stroke: var(--chart-surface);
    stroke-width: 1.75;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 1;
    stroke-dashoffset: 1;
    opacity: 0;
    transition: stroke-dashoffset var(--motion-base) var(--motion-ease), opacity var(--motion-fast) linear;
  }
  .box[data-on]:not([data-mixed]) .check,
  .box[data-mixed] .dash { stroke-dashoffset: 0; opacity: 1; }
  .hit-input:focus-visible + .box {
    outline: var(--focus-width) solid var(--focus-ring);
    outline-offset: 2px;
  }
  .hit-input:active + .box { transform: scale(0.95); }
  th[data-numeric], td[data-numeric] {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .sort {
    display: flex;
    width: 100%;
    height: 32px;
    align-items: center;
    justify-content: flex-start;
    margin: 0;
    padding: 0 var(--space-4);
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    letter-spacing: inherit;
    cursor: pointer;
  }
  th[data-numeric] .sort { justify-content: flex-end; }
  .editable-sort { display: flex; min-height: 44px; align-items: center; padding-left: var(--space-4); }
  .sort.icon-only { width: 40px; flex: none; justify-content: center; padding: 0; }
  /* A number column's head ends where its values end. Its sort button is drawn once, before
     the words on wide pages and after them on phones (staging), so Tab order follows what the
     reader sees. */
  .sort[data-placement="before"] { display: none; }
  th[data-numeric] .editable-sort { justify-content: flex-end; padding-right: var(--space-4); }
  th:last-child .editable-sort { padding-right: 0; }
  .sort-inner { display: inline-flex; align-items: center; gap: 6px; }
  th[data-numeric] .sort-inner { flex-direction: row-reverse; }
  .sort-icon {
    position: relative;
    display: grid;
    width: 16px;
    height: 16px;
    flex: none;
    place-items: center;
  }
  .sort-hint, .sort-arrow {
    position: absolute;
    inset: 0;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .sort-hint {
    color: var(--chart-muted);
    opacity: 0;
    transition: opacity var(--motion-fast) linear;
  }
  .sort-arrow {
    color: var(--text-strong);
    opacity: 0;
    filter: blur(3px);
    transform: scale(0.6) rotate(0deg);
    transition:
      opacity var(--motion-base) var(--motion-ease),
      filter var(--motion-base) var(--motion-ease),
      transform 280ms cubic-bezier(0.16, 1.4, 0.3, 1);
  }
  th[aria-sort="ascending"] .sort-arrow {
    opacity: 1;
    filter: blur(0);
    transform: scale(1) rotate(0deg);
  }
  th[aria-sort="descending"] .sort-arrow {
    opacity: 1;
    filter: blur(0);
    transform: scale(1) rotate(180deg);
  }
  .sort:focus-visible {
    outline: var(--focus-width) solid var(--focus-ring);
    outline-offset: -2px;
  }
  .sort:focus-visible .sort-hint { opacity: 0.6; }
  th[data-sorted] .sort-hint { opacity: 0; }
  .empty {
    height: 120px;
    color: var(--chart-muted);
    font-weight: var(--weight-small);
    text-align: center;
  }
  .footer {
    display: flex;
    height: 44px;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin: 0;
    padding: 0 var(--space-2) 0 var(--space-4);
    border-top: var(--border-hairline) solid var(--chart-grid);
    color: var(--chart-muted);
    font-size: var(--type-sm);
    font-weight: var(--weight-small);
    line-height: 1.4;
  }
  .count { margin: 0; font-variant-numeric: tabular-nums; }
  .count[data-active] { color: var(--text-strong); }
  .clear {
    height: 30px;
    flex: none;
    margin: 0;
    border: 0;
    border-radius: var(--radius-md);
    padding: 0 var(--space-3);
    background: transparent;
    color: var(--text-strong);
    font: inherit;
    font-size: var(--type-sm);
    font-weight: var(--weight-small);
    cursor: pointer;
  }
  .header-copy { display: contents; }
  /* Only user-sized columns need clipping: authored layouts retain their text treatment. */
  table[data-resized] .header-copy {
    display: block;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  table[data-resized] .sort-inner { min-width: 0; max-width: 100%; }
  table[data-resized] .header-copy :global(.ed) {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  table[data-resized] .header-copy:focus-within { overflow: visible; }
  table[data-resized] .header-copy :global(.ed:focus) { overflow-x: auto; text-overflow: clip; }
  table[data-resized] .cell-copy > :global(.ed),
  table[data-resized] .cell-copy > :global(span:last-child) {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  table[data-resized] .cell-copy > :global(.ed:focus) { overflow-x: auto; text-overflow: clip; }
  .resize-handle {
    position: absolute;
    z-index: 4;
    top: 0;
    right: 0;
    bottom: 0;
    width: var(--space-3);
    cursor: col-resize;
    touch-action: none;
    user-select: none;
    outline: none;
  }
  .resize-handle::after {
    content: "";
    position: absolute;
    right: 0;
    top: var(--space-2);
    bottom: var(--space-2);
    width: var(--border-strong);
    background: var(--accent-bright);
    opacity: 0;
    transition: opacity var(--motion-fast) var(--motion-ease);
  }
  th:hover > .resize-handle::after,
  .resize-handle:focus-visible::after,
  .resize-handle.resizing::after { opacity: 1; }
  .resize-handle:focus-visible { outline: var(--border-strong) solid var(--accent-bright); outline-offset: calc(-1 * var(--border-strong)); }
  .reduced .resize-handle::after { transition: none; }
  @media (hover: none), (pointer: coarse) {
    /* A trailing sort button keeps its center clear of the wider touch resize edge. */
    .sort[data-placement="after"] { margin-right: var(--space-2); }
    .resize-handle { width: var(--space-5); }
    .resize-handle::after { opacity: 0.6; }
  }
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }
  @media (hover: hover) and (pointer: fine) {
    tbody tr:hover td {
      --cell-fill: color-mix(in srgb, var(--text-strong) 3%, var(--cell-base));
    }
    tbody tr[data-selected]:hover td {
      --cell-fill: color-mix(in srgb, var(--accent-bright) 12%, var(--cell-base));
    }
    tbody tr:has(.empty):hover td { --cell-fill: var(--cell-base); }
    .clear:hover { background: color-mix(in srgb, var(--text-strong) 6%, transparent); }
    th[aria-sort="none"] .sort:hover { color: var(--text-strong); }
    th[aria-sort="none"] .sort:hover .sort-hint { opacity: 0.6; }
  }
  .reduced .sort-arrow,
  .reduced .sort-hint,
  .reduced .fill,
  .reduced .glyph path,
  .reduced .box,
  .reduced th,
  .reduced td {
    transition: none;
    filter: none;
  }
  .reduced .hit-input:active + .box { transform: none; }
  /* Aligned columns remain available at every page width. */
  @container page (min-width: 0px) {
    /* The sort control takes its own space inside each number column, clear of
       the previous column's resize edge. Header labels still end above their values. */
    th[data-numeric] .sort:not(.icon-only),
    th[data-numeric] .editable-sort { padding: 0 var(--space-2) 0 0; }
    td[data-numeric]:not([data-primary]) { padding: 0 var(--space-2) 0 6px; }
    /* The sort glyph keeps clear space on both sides. */
    th[data-numeric] :is(.sort-hint, .sort-arrow) { inset: 2px; width: 12px; height: 12px; }
    th[data-numeric] .sort-inner { gap: 0; }
    th[data-numeric] .sort-inner .sort-icon { margin: 0; }
    th[data-numeric] .sort[data-placement="before"] { display: flex; width: 14px; }
    th[data-numeric] .sort[data-placement="after"] { display: none; }
    th[data-numeric] .sort.icon-only .sort-icon { margin: 0 -1px; }
    /* A table too wide to fit scrolls, and its label column stays in view. */
    table:not([data-selectable]) :is(th[data-key="0"], td[data-primary]) { position: sticky; left: 0; }
    .scroller[data-scrolled] table:not([data-selectable]) :is(th[data-key="0"], td[data-primary]) {
      z-index: 5;
      box-shadow: inset calc(-1 * var(--border-hairline)) 0 var(--chart-grid);
    }
  }
  @container page (max-width: 620px) {
    .table-block { padding: var(--space-5) var(--space-4); border-radius: var(--radius-lg); }
    .caption { padding-inline: var(--space-2); }
  }
</style>
