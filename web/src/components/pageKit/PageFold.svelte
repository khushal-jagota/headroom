<script lang="ts">
  /** The page fold on the page this is placed in (lib/pageKit/pageFold.ts): above the
   *  composer zone every line is whole or not shown, --page-fold-clear above the reading
   *  area's bottom, at every scroll position and for a page of any length. A unit waiting
   *  below the fold is drawn invisible rather than removed, so the page's layout, focus order
   *  and scroll length never change; units fade in and out over 120ms as they cross the line,
   *  and a unit that takes focus is scrolled whole above it. The reading area is cut at the
   *  boundary where its lines stop (--page-fold-cut, from its bottom edge): a card going on
   *  under the zone is drawn to that row boundary and its box fades out over 32px below it. */
  import { onMount } from "svelte";
  import { foldCut, foldedUnits, pageUnits, PAGE_FOLD_HEADS } from "../../lib/pageKit/pageFold";

  let anchor = $state<HTMLSpanElement | null>(null);

  onMount(() => {
    const scroller = anchor?.closest<HTMLElement>(".manager-doc, .ticket-doc");
    if (!scroller) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let folded = new Set<Element>();
    let frame = 0;

    const clear = () => {
      for (const element of folded) element.removeAttribute("data-page-folded");
      folded = new Set();
      scroller.style.removeProperty("--page-fold-cut");
    };
    const measure = () => {
      frame = 0;
      if (scroller.closest('[data-conversation-dock-state="opened"]') || scroller.clientHeight === 0) { clear(); return; }
      const box = scroller.getBoundingClientRect();
      // The clear is a calc() of tokens, so it is measured as a length rather than parsed.
      const room = anchor?.getBoundingClientRect().height ?? 0;
      const units = pageUnits(scroller);
      const measured = units.map((unit) => {
        const rect = unit.getBoundingClientRect();
        return { top: rect.top, bottom: rect.bottom, head: unit.matches(PAGE_FOLD_HEADS) };
      });
      const decided = foldedUnits(measured, box.bottom - room, box.top);
      scroller.style.setProperty("--page-fold-cut", `${box.bottom - foldCut(measured, decided, box.bottom - room)}px`);
      const next = new Set<Element>();
      units.forEach((unit, index) => { if (decided[index]) next.add(unit); });
      // A unit crossing the fold fades rather than popping, in either direction.
      const fade = (element: Element, from: number, to: number) => {
        if (!reduced.matches && element instanceof HTMLElement) element.animate([{ opacity: from }, { opacity: to }], { duration: 120, easing: "ease-out" });
      };
      for (const element of folded) {
        if (next.has(element)) continue;
        element.removeAttribute("data-page-folded");
        fade(element, 0, 1);
      }
      for (const element of next) {
        if (folded.has(element)) continue;
        element.setAttribute("data-page-folded", "");
        fade(element, 1, 0);
      }
      folded = next;
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };

    const resize = new ResizeObserver(schedule);
    resize.observe(scroller);
    for (const child of scroller.children) resize.observe(child);
    const mutation = new MutationObserver(schedule);
    mutation.observe(scroller, { childList: true, subtree: true, characterData: true });
    const dock = scroller.closest("[data-conversation-dock-state]");
    const dockState = new MutationObserver(schedule);
    if (dock) dockState.observe(dock, { attributes: true, attributeFilter: ["data-conversation-dock-state"] });
    scroller.addEventListener("scroll", schedule, { passive: true });
    scroller.addEventListener("toggle", schedule, true);
    // A unit that takes focus below the fold is scrolled up until it stands whole above it.
    const focus = (event: FocusEvent) => {
      const unit = (event.target as Element | null)?.closest("[data-page-folded]");
      if (unit) unit.scrollIntoView({ block: "nearest" });
    };
    scroller.addEventListener("focusin", focus);
    // Text measures again once the page's own face has loaded.
    void document.fonts?.ready.then(schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      mutation.disconnect();
      dockState.disconnect();
      scroller.removeEventListener("scroll", schedule);
      scroller.removeEventListener("toggle", schedule, true);
      scroller.removeEventListener("focusin", focus);
      clear();
    };
  });
</script>

<span bind:this={anchor} class="page-fold-anchor" aria-hidden="true"></span>

<style>
  /* The anchor finds the reading area and measures the fold's clear; it draws nothing and
     takes no room. */
  .page-fold-anchor { position:absolute; width:0; height:var(--page-fold-clear); overflow:hidden; visibility:hidden; pointer-events:none; }
</style>
