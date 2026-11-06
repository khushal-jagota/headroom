<script lang="ts">
  import MachineActivityMark from "./machines/MachineActivityMark.svelte";
  import MachineIdentity from "./machines/MachineIdentity.svelte";
  import { macActivityKnown, macAvailability, machineName } from "../lib/ticketMachines";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { RAIL_HOVER_RECAP_MAX_LINES, clampToWords } from "../lib/workspaceRailInteraction";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import RecapSlot from "./RecapSlot.svelte";
  /* ``recapTarget`` is the agent that can be asked for a fresh recap.
   * ``onKeepOpen`` and ``onLeave`` hand the panel the same hover intent the rows use, so
   * the pointer can cross the gap and press what is inside without the panel closing. */
  let { visible, instant, target, title, kicker, recap = "", worker = null, rail, signal = undefined, conversationId = null, recapTarget = null, onKeepOpen, onLeave, element = $bindable<HTMLDivElement | null>(null) }: {
    visible: boolean; instant: boolean; target: HTMLElement | null; title: string; kicker: string;
    recap?: string; worker?: WorkItemTicketFacts | null;
    rail: HTMLElement | null; cards?: readonly WorkItemTicketFacts[];
    signal?: Partial<ConversationSignal>; conversationId?: string | null;
    recapTarget?: { kind: "ticket" | "manager" | "chief"; id: string } | null;
    onKeepOpen?: () => void; onLeave?: () => void;
    element?: HTMLDivElement | null;
  } = $props();
  let currentWorker = $derived(worker);
  const health=createQuery(() => ({...queries.executorHealth(),enabled:worker?.locale === "macos"}));
  let unknownMac = $derived(worker?.locale === "macos" && (health.isError || !macActivityKnown(health.data)));
  let stageSentence = $derived(unknownMac ? macAvailability(health.isError ? undefined : health.data) : currentWorker ? workspaceWorkerStageSentence(currentWorker) : "");
  let recapText = $derived(currentWorker ? currentWorker.recap ?? "" : recap);
  let recapIdentity = $derived(recapTarget ? `${recapTarget.kind}:${recapTarget.id}` : null);
  let pointerVisit = $state<{ target: HTMLElement | null; recapIdentity: string | null } | null>(null);
  let holdRecapHeight = $derived(visible && pointerVisit !== null && pointerVisit.target === target && pointerVisit.recapIdentity === recapIdentity);
  $effect(() => {
    if (!visible || (pointerVisit && (pointerVisit.target !== target || pointerVisit.recapIdentity !== recapIdentity))) pointerVisit = null;
  });
  let recapElement = $state<HTMLDivElement | null>(null);
  let recapLines = $state(1);
  // Measure the fixed content, never the clamped recap. This keeps the budget
  // independent of the height that the budget itself produces.
  $effect(() => {
    recapText; stageSentence; title; kicker;
    if (!visible || !element || !recapElement) return;
    const panel = element;
    const recapBox = recapElement;
    const budget = () => {
      const css = getComputedStyle(panel);
      const recapCss = getComputedStyle(recapBox);
      const px = (value: string) => Number.parseFloat(value) || 0;
      const edges = (style: CSSStyleDeclaration) => px(style.paddingTop) + px(style.paddingBottom) + px(style.borderTopWidth) + px(style.borderBottomWidth);
      const outerHeight = (child: Element) => {
        const style = getComputedStyle(child);
        return child.getBoundingClientRect().height + px(style.marginTop) + px(style.marginBottom);
      };
      // The ask shares the recessed block with the recap, so it is fixed content that
      // happens to sit inside the box being measured.
      const ask = recapBox.querySelector("[data-request-recap]");
      const fixed = [...panel.children].filter(child => child !== recapBox);
      if (ask) fixed.push(ask);
      const fixedHeight = fixed.reduce((height, child) => height + outerHeight(child), 0);
      const available = px(css.maxHeight) - edges(css) - fixedHeight - edges(recapCss) - px(recapCss.marginTop) - px(recapCss.marginBottom);
      recapLines = Math.min(RAIL_HOVER_RECAP_MAX_LINES, Math.max(0, Math.floor(available / px(recapCss.lineHeight))));
    };
    const observer = new ResizeObserver(budget);
    for (const child of panel.children) if (child !== recapBox) observer.observe(child);
    void tick().then(budget);
    window.addEventListener("resize", budget);
    return () => { observer.disconnect(); window.removeEventListener("resize", budget); };
  });
  let left = $state(0);
  let top = $state(0);
  /* The panel can only take the pointer where it is clear of the rail. A narrow window
   * clamps it back over the rows, and a panel that took the pointer there would cover
   * the row the pointer is travelling to. Hovering the rail is the common act and wins. */
  let clearOfRail = $state(false);
  $effect(() => {
    if (!target || !rail || !visible || !element) return;
    const currentTarget = target;
    const currentRail = rail;
    const currentPanel = element;
    /* The panel never covers the row it describes or the conversation dock. Beside the
     * rail it opens past the rail's edge, level with its row. A row in a page list opens
     * below the row, or above it when the dock leaves no room underneath. */
    const place = () => {
      const rect = currentTarget.getBoundingClientRect();
      const width = currentPanel.offsetWidth;
      const height = currentPanel.offsetHeight;
      // The floor is the composer's own field: the panel may pass over the dock's quiet
      // status line, so it can stay near a low row, but never over where you type.
      const dock = (document.querySelector(".conversation-layer .chat-box") ?? document.querySelector(".conversation-layer"))?.getBoundingClientRect();
      const floor = Math.min(window.innerHeight, dock && dock.height > 0 ? dock.top : window.innerHeight) - 12;
      if (currentRail !== currentTarget) {
        const railRight = currentRail.getBoundingClientRect().right;
        // Next to its row: 8px past the rail's edge, its top level with the row's top.
        left = Math.max(12, Math.min(railRight + 8, window.innerWidth - width - 12));
        top = Math.max(12, Math.min(rect.top, floor - height));
        clearOfRail = left >= railRight;
        return;
      }
      // A row in a page list hugs its words, so the panel opens to the right of the row's end,
      // centred on the row and kept inside the window and above the composer. It never
      // overlaps its own row; where the window leaves no room to the right it opens under the
      // row, or over it when the composer leaves no room under it.
      const row = currentTarget.querySelector<HTMLElement>(".ticket-row") ?? currentTarget;
      const box = row.getBoundingClientRect();
      // Beside its row the panel stays with its list (never more than 10px above the list's
      // own heading) and clear of the composer by 8px.
      const listTop = (currentTarget.closest(".page-tickets") ?? currentTarget.closest("section") ?? currentTarget).getBoundingClientRect().top;
      const besideFloor = floor + 4;
      const besideTop = Math.max(12, listTop - 10, Math.min(box.top + box.height / 2 - height / 2, besideFloor - height));
      // Beside the row it also clears the rows and headings it lies level with, which hug
      // their words too, so it covers only empty column.
      const list = currentTarget.closest("[data-manager-children], .manager-children") ?? currentTarget.parentElement;
      let clear = box.right;
      for (const other of list?.querySelectorAll<HTMLElement>(".ticket-row, summary") ?? []) {
        const near = other.getBoundingClientRect();
        if (near.bottom > besideTop && near.top < besideTop + height) {
          if (other.matches("summary")) { const words = document.createRange(); words.selectNodeContents(other); for (const line of words.getClientRects()) if (line.width) clear = Math.max(clear, line.right); }
          else clear = Math.max(clear, near.right);
        }
      }
      if (clear + 8 + width <= window.innerWidth - 12) {
        left = clear + 8;
        top = besideTop;
      } else {
        left = Math.max(12, Math.min(box.left, window.innerWidth - width - 12));
        top = box.bottom + 6 + height <= floor ? box.bottom + 6 : Math.max(12, box.top - 6 - height);
      }
      // Beside its row the pointer can cross straight to it and press the ask inside.
      clearOfRail = left >= box.right;
    };
    // Manifest arrival can change the height after the panel is already visible.
    const observer = new ResizeObserver(place);
    observer.observe(currentPanel);
    void tick().then(place);
    // A scroll no longer dismisses the panel, so it follows its row instead.
    window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  });
</script>
<div bind:this={element} class="rail-hover-panel" class:visible class:instant class:interactive={visible && clearOfRail} style:left={`${left}px`} style:top={`${top}px`} data-rail-hover-panel aria-hidden={!visible}
  onpointerenter={(event) => {
    if (visible && event.pointerType !== "touch" && window.matchMedia("(hover: hover) and (pointer: fine)").matches)
      pointerVisit = { target, recapIdentity };
    onKeepOpen?.();
  }}
  onpointerleave={(event) => {
    pointerVisit = null;
    // A finger lifting off a control inside is not leaving. The screen dismisses a
    // touch preview on an outside press or a scroll, as it does for goal previews.
    if (event.pointerType !== "touch") onLeave?.();
  }}
  onfocusin={() => onKeepOpen?.()}
  onfocusout={(event) => {
    // Focus landing somewhere outside is someone leaving. Focus landing nowhere is the
    // control they just pressed being replaced by what it did, and they are still here.
    const moved = event.relatedTarget as Node | null;
    if (moved !== null && !element?.contains(moved)) onLeave?.();
  }}>
  <!-- The machine's mark leads the eyebrow, as it leads the Ticket's line on its page; its name
       is the mark's accessible name and tooltip. -->
  <div class="kicker">{#if currentWorker?.locale}<span class="kicker-machine" title={`Runs on ${machineName(currentWorker.locale)}`}><MachineIdentity locale={currentWorker.locale} /></span>{/if}<span class="kicker-place">{kicker}</span></div>
  <div class="title"><span class="title-text">{currentWorker?.title ?? title}</span><MachineActivityMark locale={currentWorker?.locale} signal={currentWorker?.conversation_signal ?? signal} conversationId={currentWorker?.conversation_id ?? conversationId} /></div>
  <!-- Where the Ticket is in its work belongs with its name, so every preview ends the same
       way: the recap, then the ask for a fresh one. -->
  {#if worker && stageSentence}<div class="doing" data-machine-stage-unknown={unknownMac ? "true" : undefined}>{stageSentence}</div>{/if}
  <!-- The box stays while there is an ask to hold, so pressing it leaves the recessed
       block where it is and the budget above keeps measuring. -->
  {#if recapText || recapTarget}
    <div bind:this={recapElement} class="recap" class:no-space={recapLines === 0 && !recapTarget}>
      {#key recapIdentity}
        <RecapSlot holdHeight={holdRecapHeight}>
          {#snippet children()}{#if recapText}<div class="recap-text" class:no-room={recapLines === 0} data-recap-text use:clampToWords={{ text: recapText, lines: recapLines }}></div>{/if}{/snippet}
        </RecapSlot>
      {/key}
      {#if recapTarget}<RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} class="recap-ask" />{/if}
    </div>
  {/if}
</div>
<style>
  /* A compact raised card on the rail's float surface: where it belongs (the machine's mark
     and the place, in the Small voice), what it is (the title in the row voice), then the
     recap as the body you opened it for, three lines at most and ending at a word, and the
     ask for a fresh one as a quiet line under it. */
  .rail-hover-panel { box-sizing:border-box; position:fixed; z-index:40; width:min(var(--rail-preview-width),calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--rail-preview-pad); border:var(--border-hairline) solid var(--rail-float-ring); border-radius:var(--radius-lg); background:var(--rail-float-surface); box-shadow:var(--rail-float-shadow); font-size:var(--type-row); line-height:normal; pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .interactive { pointer-events:auto; }
  .instant { transition:none; }
  .kicker { display:flex; align-items:center; gap:6px; min-width:0; font-size:var(--type-small); font-weight:var(--weight-small); line-height:16px; color:var(--tone-tertiary); margin-bottom:4px; }
  .kicker-place { min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  .kicker-machine { flex:none; display:inline-flex; align-items:center; }
  .kicker-machine :global(.machine-identity) { width:14px; height:14px; color:inherit; }
  .title { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); font-size:var(--type-body); font-weight:var(--weight-title); line-height:20px; color:var(--tone-strong); overflow-wrap:anywhere; }
  .title-text { flex:1; min-width:0; }
  /* The recap is the body the preview is opened for: the page's body voice, given its own
     space, and the only paragraph in the panel. */
  .recap { margin-top:var(--space-2); font-size:var(--rail-preview-recap-size); font-weight:var(--weight-body); line-height:var(--leading-body); color:var(--tone-secondary); }
  .recap-text { overflow:hidden; overflow-wrap:break-word; }
  /* Nowhere to put a recap. The box goes with it unless it is also holding the ask. */
  .recap.no-space, .recap-text.no-room { display:none; }
  /* The ask is a small outline button: the preview's one action, clearly an endpoint. */
  /* Actions sit at the right, the most important furthest right (owner rule). */
  .recap :global(.recap-ask) { display:flex; justify-content:flex-end; margin-top:var(--space-3); }
  .recap :global(.recap-ask .request-recap-action) { height:24px; padding:0 var(--space-2); border-radius:6px; background:transparent; box-shadow:inset 0 0 0 var(--border-hairline) var(--button-outline-edge); color:var(--tone-primary); font-size:var(--type-small); font-weight:var(--weight-small); line-height:16px; }
  .recap :global(.recap-ask .request-recap-action:hover) { background:var(--button-outline-fill-hover); color:var(--tone-strong); }
  .doing { margin-top:var(--space-1); font-size:var(--type-small); font-weight:var(--weight-small); line-height:16px; color:var(--tone-tertiary); }
  @media (prefers-reduced-motion:reduce) { .rail-hover-panel { transition:none; transform:none; } }
</style>
