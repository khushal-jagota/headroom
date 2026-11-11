<script lang="ts">
  import ManagerGoalPreview from "./goals/ManagerGoalPreview.svelte";
  import type {GoalResponse} from "../lib/goals/contracts";
  import { skipPreviewTabStops } from "../lib/previewTabOrder";
  import MachineActivityMark from "./machines/MachineActivityMark.svelte";
  import MachineIdentity from "./machines/MachineIdentity.svelte";
  import { macActivityKnown, macPresenceLabel, macTicketActivityLabel } from "../lib/ticketMachines";
  import { MACHINE_ACTIVITY_UNKNOWN } from "../lib/machines/config";
  import type { ConversationSignal } from "../lib/conversationSignal";
  import { tick } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import { RAIL_HOVER_RECAP_MAX_LINES } from "../lib/workspaceRailInteraction";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import RecapSlot from "./RecapSlot.svelte";
  /* ``recapTarget`` is the agent that can be asked for a fresh recap.
   * ``onKeepOpen`` and ``onLeave`` hand the panel the same hover intent the rows use, so
   * the pointer can cross the gap and press what is inside without the panel closing. */
  let { goals = [], visible, instant, target, title, kicker, recap = "", worker = null, rail, signal = undefined, conversationId = null, recapTarget = null, onKeepOpen, onLeave, onDismiss, element = $bindable<HTMLDivElement | null>(null) }: {
    goals?: GoalResponse[]; visible: boolean; instant: boolean; target: HTMLElement | null; title: string; kicker: string;
    recap?: string; worker?: WorkItemTicketFacts | null;
    rail: HTMLElement | null; cards?: readonly WorkItemTicketFacts[];
    signal?: Partial<ConversationSignal>; conversationId?: string | null;
    recapTarget?: { kind: "ticket" | "manager" | "chief"; id: string } | null;
    onKeepOpen?: () => void; onLeave?: () => void; onDismiss?: () => void;
    element?: HTMLDivElement | null;
  } = $props();
  let currentWorker = $derived(worker);
  const health=createQuery(() => ({...queries.executorHealth(),enabled:worker?.locale === "macos"}));
  let unknownMac = $derived(worker?.locale === "macos" && (health.isError || !macActivityKnown(health.data)));
  let macHealth = $derived(health.isError ? undefined : health.data);
  let stageSentence = $derived(unknownMac ? (worker ? macTicketActivityLabel(macHealth, worker) : null) ?? macPresenceLabel(macHealth) ?? MACHINE_ACTIVITY_UNKNOWN
    : currentWorker ? workspaceWorkerStageSentence(currentWorker) : "");
  let recapText = $derived(currentWorker ? currentWorker.recap ?? "" : recap);
  let recapIdentity = $derived(recapTarget ? `${recapTarget.kind}:${recapTarget.id}` : null);
  let pointerVisit = $state<{ target: HTMLElement | null; recapIdentity: string | null } | null>(null);
  let holdRecapHeight = $derived(visible && pointerVisit !== null && pointerVisit.target === target && pointerVisit.recapIdentity === recapIdentity);
  $effect(() => {
    if (!visible || (pointerVisit && (pointerVisit.target !== target || pointerVisit.recapIdentity !== recapIdentity))) pointerVisit = null;
  });
  let recapElement = $state<HTMLDivElement | null>(null);
  let recapLines = $state(1);
  let goalsMaxHeight = $state<number | null>(null);
  // Measure the fixed content, never the clamped recap. This keeps the budget
  // independent of the height that the budget itself produces.
  $effect(() => {
    recapText; stageSentence; title; kicker; goals; holdRecapHeight;
    if (!visible || !element) return;
    const panel = element;
    const recapBox = recapElement;
    const budget = () => {
      const css = getComputedStyle(panel);
      const recapCss = recapBox ? getComputedStyle(recapBox) : null;
      const px = (value: string) => Number.parseFloat(value) || 0;
      const edges = (style: CSSStyleDeclaration) => px(style.paddingTop) + px(style.paddingBottom) + px(style.borderTopWidth) + px(style.borderBottomWidth);
      const outerHeight = (child: Element) => {
        const style = getComputedStyle(child);
        return child.getBoundingClientRect().height + px(style.marginTop) + px(style.marginBottom);
      };
      // The ask shares the recessed block with the recap, so it is fixed content that
      // happens to sit inside the box being measured.
      const ask = recapBox?.querySelector("[data-request-recap]");
      const fixed = [...panel.children].filter(child => child !== recapBox);
      if (ask) fixed.push(ask);
      const fixedHeight = fixed.reduce((height, child) => height + outerHeight(child), 0);
      const recapEdges = recapCss ? edges(recapCss) + px(recapCss.marginTop) + px(recapCss.marginBottom) : 0;
      const goalsBox=panel.querySelector<HTMLElement>('[data-manager-goals]');
      if(goalsBox){
        const goalsCss=getComputedStyle(goalsBox);
        const otherFixedHeight=fixedHeight-outerHeight(goalsBox);
        // Keep the Ask action still while the pointer is inside. The recap slot
        // retains its visit height, so an expanded chart scrolls in the room left.
        const heldRecapHeight=holdRecapHeight?(recapBox?.querySelector('[data-recap-slot]')?.getBoundingClientRect().height??0):0;
        goalsMaxHeight=Math.max(0,px(css.maxHeight)-edges(css)-otherFixedHeight-recapEdges-px(goalsCss.marginTop)-px(goalsCss.marginBottom)-heldRecapHeight);
      }
      if (recapCss) {
        const available = px(css.maxHeight) - edges(css) - fixedHeight - recapEdges;
        recapLines = Math.min(RAIL_HOVER_RECAP_MAX_LINES, Math.max(0, Math.floor(available / px(recapCss.lineHeight))));
      }
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
    /* Beside the rail the panel opens past the rail's edge, level with its row, clear of the
     * composer. A row in a page list places it as staging did. */
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
      // A row in a page list, as on staging: the panel opens 14px past the row's own box (the
      // list's full width), 8px above the row's top, kept inside the window.
      left = Math.max(12, Math.min(rect.right + 14, window.innerWidth - width - 12));
      top = Math.max(12, Math.min(rect.top - 8, window.innerHeight - height - 12));
      clearOfRail = left >= rect.right;
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
<div bind:this={element} use:skipPreviewTabStops class="rail-hover-panel" class:visible class:instant class:interactive={visible && clearOfRail} style:left={`${left}px`} style:top={`${top}px`} data-rail-hover-panel aria-hidden={!visible}
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
  <div class="kicker">{#if currentWorker?.locale}<MachineIdentity locale={currentWorker.locale} />{/if}<span>{kicker}</span></div>
  <div class="title"><span class="title-text">{currentWorker?.title ?? title}</span><MachineActivityMark agentHold={currentWorker ?? undefined} locale={currentWorker?.locale} signal={currentWorker?.conversation_signal ?? signal} conversationId={currentWorker?.conversation_id ?? conversationId} /></div>
  <!-- The box stays while there is an ask to hold, so pressing it leaves the recessed
       block where it is and the budget above keeps measuring. -->
  {#if recapText || recapTarget}
    <div bind:this={recapElement} class="recap" class:no-space={recapLines === 0 && !recapTarget}>
      {#key recapIdentity}
        <RecapSlot holdHeight={holdRecapHeight}>
          {#snippet children()}{#if recapText}<div class="recap-text" class:no-room={recapLines === 0} data-recap-text style:--recap-lines={recapLines}>{recapText}</div>{/if}{/snippet}
        </RecapSlot>
      {/key}
      {#if recapTarget}<RequestRecapAction kind={recapTarget.kind} id={recapTarget.id} variant="ghost" class="recap-ask" />{/if}
    </div>
  {/if}
  {#if !worker && goals.length}<div class="manager-goals" style:max-height={goalsMaxHeight===null?undefined:`${goalsMaxHeight}px`} data-manager-goals><ManagerGoalPreview {goals} {onDismiss}/></div>{/if}
  {#if worker && stageSentence}<div class="doing" data-machine-stage-unknown={unknownMac ? "true" : undefined}>{stageSentence}</div>{/if}
</div>
<style>
  /* Staging's preview, on the rail's float surface (a fill and a soft shadow, no ring): where it
     belongs, what it is, the recap in its recessed field (up to five lines, the full text
     kept), the ask at the right inside it as a filled secondary button, and a Ticket's stage
     under it all. */
  .rail-hover-panel { box-sizing:border-box; font-size:var(--type-sm); line-height:normal; position:fixed; z-index:40; width:min(var(--rail-preview-width),calc(100vw - 24px)); max-height:74vh; overflow:hidden; padding:var(--rail-preview-pad); border:0; border-radius:var(--radius-lg); background:var(--rail-preview-surface); box-shadow:var(--rail-preview-shadow); pointer-events:none; opacity:0; transform:translateX(-4px); transition:opacity var(--motion-base) var(--motion-ease),transform var(--motion-base) var(--motion-ease); }
  .visible { opacity:1; transform:none; }
  .interactive { pointer-events:auto; }
  .instant { transition:none; }
  .kicker { display:flex; align-items:center; gap:var(--space-2); font-size:var(--type-small); font-weight:var(--weight-small); color:var(--tone-tertiary); margin-bottom:5px; }
  .title { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); font-size:var(--type-body); font-weight:var(--weight-title); line-height:1.4; color:var(--tone-strong); overflow-wrap:anywhere; }
  .title-text { flex:1; min-width:0; }
  /* Staging's recessed field (owner), one softer step deep: #161616 under the #202020 preview, and
     its 'Ask for a recap' a quiet text action, so the preview stays light (round 17). */
  .recap { margin-top:var(--space-3); padding:var(--space-3); background:var(--rail-preview-well); border-radius:var(--radius-md); font-size:var(--rail-preview-recap-size); line-height:1.5; color:var(--tone-primary); }
  .recap-text { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:var(--recap-lines); line-clamp:var(--recap-lines); overflow:hidden; overflow-wrap:anywhere; }
  /* Nowhere to put a recap. The box goes with it unless it is also holding the ask. */
  .recap.no-space, .recap-text.no-room { display:none; }
  /* The ask lives inside the recessed block, under the line it replaces, at the right. */
  .recap :global(.recap-ask) { display:flex; justify-content:flex-end; margin-top:var(--space-2); font-size:var(--type-small); }
  .manager-goals { margin-top:var(--space-3); overflow:auto; overscroll-behavior:contain; scrollbar-width:thin; }
  .doing { margin-top:var(--space-3); font-size:var(--type-small); color:var(--tone-secondary); }
  @media (prefers-reduced-motion:reduce) { .rail-hover-panel { transition:none; transform:none; } }
</style>
