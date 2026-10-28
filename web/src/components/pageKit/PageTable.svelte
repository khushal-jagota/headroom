<script lang="ts">
  import {getContext} from "svelte";
  import GoalInlineEdit from "../goals/GoalInlineEdit.svelte";
  import {PAGE_CONTEXT,type PageContext} from "../../lib/pageKit/context";
  const context=getContext<PageContext | undefined>(PAGE_CONTEXT);
  import { fade } from "svelte/transition";
  import type { TableBlock } from "../../lib/pageKit";

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
  let widths = $state<number[] | null>(null);
  let band = $state<{ left: number; width: number } | null>(null);

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
      const total = table.getBoundingClientRect().width;
      const cells = [...table.querySelectorAll<HTMLTableCellElement>("thead th[data-key]")];
      if (!total || cells.length !== block.columns.length) return false;
      widths = cells.map((cell) => (cell.getBoundingClientRect().width / total) * 100);
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

  function onRowClick(event: MouseEvent, key: number) {
    if (!selectable) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("a, button, input, label, select, textarea")) return;
    if (window.getSelection()?.toString()) return;
    toggleRow(key, event.shiftKey);
    (event.currentTarget as HTMLElement).querySelector<HTMLInputElement>("input[type=checkbox]")?.focus({ preventScroll: true });
  }

  function onRowDown(event: MouseEvent) {
    shiftHeld = event.shiftKey;
    if (!selectable || !event.shiftKey) return;
    const target = event.target;
    if (target instanceof Element && target.closest("input, label")) return;
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
    if (event.key === " " || event.key === "Enter") shiftHeld = event.shiftKey;
    if (event.key === "Escape" && selectable && selectedCount > 0) {
      event.preventDefault();
      clearSelection();
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.dataset.nav) return;
    const nav = target.dataset.nav;
    const list = (name: string) => [...node.querySelectorAll<HTMLElement>(`[data-nav="${name}"]`)];
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
  {#if block.caption}<p class="caption">{block.caption}</p>{/if}
  <div class="card">
    <div class="scroller">
      {#if band}<span class="band" style:left="{band.left}px" style:width="{band.width}px" aria-hidden="true"></span>{/if}
      <table bind:this={tableEl} data-fixed={widths ? "" : undefined} data-selectable={selectable ? "" : undefined}>
        {#if block.caption}<caption class="sr">{block.caption}</caption>{/if}
        {#if widths || selectable}
          <colgroup>
            {#if selectable}<col class="select-col" />{/if}
            {#if widths}
              {#each widths as width, index (`col-${index}`)}
                <col style:width="{width}%" />
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
            {#each block.columns as column, index (column)}
              <th
                scope="col"
                data-key={column}
                aria-sort={ariaSort(index)}
                data-sorted={sort?.index === index ? "" : undefined}
                data-numeric={numeric[index] ? "" : undefined}
              >
                <button type="button" class="sort" data-nav="head" aria-label={sortLabel(index)} onclick={() => sortBy(index)}>
                  <span class="sort-inner">
                    <span>{column}</span>
                    <span class="sort-icon" aria-hidden="true">
                      <svg class="sort-hint" viewBox="0 0 16 16"><path d="M8 12.25V3.75M4.75 7 8 3.75 11.25 7" /></svg>
                      <svg class="sort-arrow" viewBox="0 0 16 16"><path d="M8 12.25V3.75M4.75 7 8 3.75 11.25 7" /></svg>
                    </span>
                  </span>
                </button>
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#if ordered.length === 0}
            <tr>
              <td class="empty" colspan={block.columns.length + (selectable ? 1 : 0)}>No rows to show</td>
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
                {#each line.cells as cell, index (block.columns[index])}
                  <td
                    data-primary={index === 0 ? "" : undefined}
                    data-sorted={sort?.index === index ? "" : undefined}
                    data-numeric={numeric[index] ? "" : undefined}
                    data-tone={block.cell_tones?.[line.key]?.[index] ?? undefined}
                  ><div class="cell-copy">{#if block.cell_tones?.[line.key]?.[index]}<span class="cell-status-dot" aria-hidden="true"></span>{/if}{#if context?.updateBlock}<GoalInlineEdit revision={context!.revision} value={cell} ariaLabel={`Edit ${block.columns[index]}`} onSave={(value, revision)=>context!.updateBlock!(block.id,old=>old.type === "table" ? {...old,rows:old.rows.map((row,r)=>r===line.key?row.map((c,i)=>i===index?value:c):row)} : old, revision)} />{:else}{cell}{/if}</div></td>
                {/each}
              </tr>
            {/each}
          {/if}
        </tbody>
      </table>
    </div>
    <div class="footer">
      <p class="count" data-active={selectable && selectedCount > 0 ? "" : undefined}>{countLabel}</p>
      {#if selectable && selectedCount > 0}
        <button type="button" class="clear" data-clear transition:fade={{ duration: reduced ? 0 : 140 }} onclick={clearSelection}>Clear</button>
      {/if}
    </div>
  </div>
  <p class="sr" role="status">{message}</p>
</div>

<style>
  .table-block { min-width: 0; }
  .caption {
    margin: 0 0 var(--space-3);
    color: var(--text-strong);
    font-size: var(--type-md);
    font-weight: 500;
    line-height: 1.4;
  }
  .card {
    --column-wash: color-mix(in srgb, var(--text-strong) 2.5%, var(--surface-2));
    --row-line: color-mix(in srgb, var(--border-color) 45%, transparent);
    position: relative;
    min-width: 0;
    overflow: hidden;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-lg);
    background: var(--surface-2);
    color: var(--text-default);
  }
  .scroller { position: relative; overflow-x: auto; }
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
    border-collapse: separate;
    border-spacing: 0;
    font-size: var(--type-sm);
    line-height: 1.4;
    text-align: left;
  }
  table[data-fixed] { table-layout: fixed; }
  th, td {
    --cell-base: var(--surface-2);
    --cell-fill: var(--cell-base);
    position: relative;
    background-color: var(--cell-fill);
    vertical-align: middle;
  }
  th[data-sorted], td[data-sorted] { --cell-base: var(--column-wash); }
  th {
    height: 44px;
    padding: 0;
    border-bottom: var(--border-hairline) solid var(--border-color);
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 500;
    white-space: nowrap;
  }
  th[data-sorted] { color: var(--text-strong); }
  td {
    height: 52px;
    padding: 0 var(--space-4);
    border-bottom: var(--border-hairline) solid var(--row-line);
    color: var(--text-default);
    font-weight: 400;
    overflow-wrap: anywhere;
  }
  td[data-primary] { color: var(--text-strong); font-weight: 500; }
  td[data-tone="good"] { color: var(--accent-done); }
  td[data-tone="attention"] { color: var(--accent-attention); }
  td[data-tone="bad"] { color: var(--accent-error); }
  td[data-tone="neutral"] { color: var(--text-faint); }
  td[data-tone] .cell-copy { display: flex; align-items: center; gap: var(--space-2); }
  .cell-status-dot { flex: none; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
  tbody tr:last-child td { border-bottom: 0; }
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
  td.select-cell .hit { height: 52px; }
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
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    background: var(--surface-2);
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
    stroke: var(--surface-2);
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
    outline: var(--border-strong) solid var(--accent-bright);
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
    height: 44px;
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
    color: var(--text-faint);
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
    outline: var(--border-strong) solid var(--accent-bright);
    outline-offset: -2px;
  }
  .sort:focus-visible .sort-hint { opacity: 0.6; }
  th[data-sorted] .sort-hint { opacity: 0; }
  .empty {
    height: 120px;
    color: var(--text-faint);
    font-weight: 400;
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
    border-top: var(--border-hairline) solid var(--border-color);
    color: var(--text-faint);
    font-size: var(--type-sm);
    font-weight: 400;
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
    font-weight: 500;
    cursor: pointer;
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
</style>
