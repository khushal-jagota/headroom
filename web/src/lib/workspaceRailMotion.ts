import { tick } from "svelte";
import type { WorkspaceMessageConversation } from "./types";
import { refreshWorkspaceRailSignals, stabilizeWorkspaceRail, type WorkspaceRail } from "./workspaceRail";

// The approved Glide timing: distance changes duration, never the easing.
export const RAIL_MOTION = {
  moveMin: 360, moveMax: 560, space: 340,
  fadeIn: 320, fadeDelay: 140, exit: 160,
  ease: "cubic-bezier(.4,.02,.2,1)", enter: "cubic-bezier(.05,.7,.1,1)", leave: "cubic-bezier(.3,0,.8,.15)"
};
export const railMoveDuration = (distance: number) => Math.round(Math.max(RAIL_MOTION.moveMin,
  Math.min(RAIL_MOTION.moveMax, 300 + Math.abs(distance) * .7)));

/** Rendered structure travels together, including independently discovered messages. */
export type WorkspaceRailPresentation = {
  rail: WorkspaceRail;
  settledOpen: boolean;
  settledMessages: Record<string, WorkspaceMessageConversation[]>;
};

type Part = { node: HTMLElement; rect: DOMRect; color: string; mark: string[] | null; parent: string | null };
function snapshot(root: HTMLElement | null): Map<string, Part> {
  return new Map(Array.from(root?.querySelectorAll<HTMLElement>("[data-rail-motion]") ?? []).map((node) => {
    const mark = node.dataset.railMotion?.startsWith("row:") ? node.querySelector<HTMLElement>(".stage-mark--current-running") : null;
    const style = mark && getComputedStyle(mark);
    return [node.dataset.railMotion!, { node, rect: node.getBoundingClientRect(), color: getComputedStyle(node).color,
      mark: style ? [style.borderTopColor, style.borderRightColor, style.borderBottomColor, style.borderLeftColor] : null,
      parent: node.parentElement?.closest<HTMLElement>("[data-rail-motion]")?.dataset.railMotion ?? null }];
  }));
}

/** Defer structure during motion or hover; conversation facts stay live during hover. */
export function createRailMotionController(root: () => HTMLElement | null,
  read: () => WorkspaceRailPresentation, apply: (presentation: WorkspaceRailPresentation) => void) {
  let pending: WorkspaceRailPresentation | null = null;
  let frozen = false;
  let busy = false;
  let disposed = false;
  const animations = new Set<Animation>();
  let media: MediaQueryList | null = null;
  const motionPreferenceChanged = () => { if (media?.matches) for (const animation of animations) animation.cancel(); };
  const reduced = () => {
    if (!media) {
      media = window.matchMedia("(prefers-reduced-motion: reduce)");
      media.addEventListener("change", motionPreferenceChanged);
    }
    return media.matches;
  };
  function animate(node: HTMLElement, frames: Keyframe[], duration: number, delay = 0, travelling = false, easing = RAIL_MOTION.ease): Promise<void> {
    if (reduced() || typeof node.animate !== "function") return Promise.resolve();
    node.classList.add("rail-moving");
    const travellingCard = travelling ? node.closest<HTMLElement>(".orchestrator-card") : null;
    if (travelling) { node.classList.add("rail-travelling"); travellingCard?.classList.add("rail-travelling-card"); }
    const animation = node.animate(frames, { duration, delay, easing, fill: "backwards" });
    animations.add(animation);
    return animation.finished.catch(() => {}).then(() => {
      animations.delete(animation);
      if (!Array.from(animations).some((active) => (active.effect as KeyframeEffect)?.target === node))
        node.classList.remove("rail-moving", "rail-travelling");
      if (travellingCard && !Array.from(animations).some((active) => {
        const target = (active.effect as KeyframeEffect)?.target;
        return target instanceof HTMLElement && target.classList.contains("rail-travelling") && travellingCard.contains(target);
      })) travellingCard.classList.remove("rail-travelling-card");
    });
  }
  async function drain(): Promise<void> {
    if (busy || frozen || disposed || !pending) return;
    busy = true;
    const previous = read();
    const next = {...pending, rail: stabilizeWorkspaceRail(pending.rail, previous.rail)}; pending = null;
    const railRoot = root();
    railRoot?.setAttribute("data-rail-motion-busy", "");
    const section = railRoot?.querySelector<HTMLElement>(".settled");
    const oldHeight = section?.getBoundingClientRect().height ?? 0;
    const oldCount = railRoot?.querySelector<HTMLElement>("[data-settled-count]")?.textContent;
    if (previous.settledOpen && !next.settledOpen && section) {
      const body = section.querySelector<HTMLElement>("[data-settled-scroll]");
      const header = section.querySelector<HTMLButtonElement>(".settled-head");
      if (body?.contains(document.activeElement)) header?.focus();
      // Keep the body mounted through the fade. Its space and chevron close in
      // the same beat; a refresh arriving meanwhile waits in the shared queue.
      const closing: Promise<void>[] = [];
      section.style.overflow = "hidden";
      closing.push(animate(section, [{height:`${oldHeight}px`}, {height:`${header?.getBoundingClientRect().height ?? 0}px`}], RAIL_MOTION.space));
      const chevron = section.querySelector<HTMLElement>(".settled-chevron");
      if (chevron) closing.push(animate(chevron, [{transform:"rotate(180deg)"}, {transform:"rotate(0deg)"}], RAIL_MOTION.space));
      if (body) {
        body.style.opacity = "0";
        closing.push(animate(body, [{opacity:1}, {opacity:0}], RAIL_MOTION.exit, 0, false, RAIL_MOTION.leave));
      }
      await Promise.all(closing);
      section.style.removeProperty("overflow");
      if (disposed) return;
      apply({...read(), settledOpen:false});
      await tick();
      pending ??= next;
      busy = false;
      railRoot?.removeAttribute("data-rail-motion-busy");
      void drain();
      return;
    }
    const first = snapshot(root());
    // Exit while the old space still exists. Headings leave with their last row.
    const remaining = new Set<string>(["card:chief", "settled-section", ...next.rail.items.map((item) => `card:${item.id}`)]);
    for (const [id, groups] of [["chief", next.rail.chiefGroups], ...next.rail.items.map((item) => [item.id, item.groups] as const)] as const) {
      for (const group of groups) {
        remaining.add(`heading:${id}:${group.key}`);
        for (const ticket of group.cards) remaining.add(`row:${ticket.id}`);
      }
    }
    if (next.settledOpen) for (const item of next.rail.settledItems) {
      remaining.add(`settled:${item.id}`);
      const messages = next.settledMessages[item.id] ?? [];
      if (messages.length) remaining.add(`settled-heading:${item.id}`);
      for (const message of messages) remaining.add(`settled-message:${item.id}:${message.conversationId}`);
    }
    const exits = [...first].filter(([key]) => !remaining.has(key));
    await Promise.all(exits.map(([, part]) => animate(part.node, [{ opacity: 1 }, { opacity: 0 }], RAIL_MOTION.exit, 0, false, RAIL_MOTION.leave)));
    if (disposed) return;
    // A pointer can arrive during an exit. Restore it, and defer the whole structure.
    if (frozen) {
      pending ??= next;
      apply(signalsOnly(pending));
      busy = false;
      railRoot?.removeAttribute("data-rail-motion-busy");
      return;
    }
    apply(next);
    await tick();
    if (disposed) return;
    const last = snapshot(root());
    const plays: Promise<void>[] = [];
    if (section) {
      const newHeight = section.getBoundingClientRect().height;
      if (Math.abs(oldHeight - newHeight) > .5) {
        section.style.overflow = "hidden";
        plays.push(animate(section, [{height:`${oldHeight}px`}, {height:`${newHeight}px`}], RAIL_MOTION.space)
          .then(() => { section.style.removeProperty("overflow"); }));
      }
      if (previous.settledOpen !== next.settledOpen) {
        const chevron = section.querySelector<HTMLElement>(".settled-chevron");
        if (chevron) plays.push(animate(chevron,
          [{transform:`rotate(${previous.settledOpen ? 180 : 0}deg)`}, {transform:`rotate(${next.settledOpen ? 180 : 0}deg)`}], RAIL_MOTION.space));
      }
      const count = section.querySelector<HTMLElement>("[data-settled-count]");
      if (count && oldCount !== undefined && oldCount !== count.textContent) {
        const outgoing = count.cloneNode(true) as HTMLElement;
        outgoing.removeAttribute("data-settled-count");
        outgoing.setAttribute("aria-hidden", "true");
        outgoing.textContent = oldCount;
        outgoing.style.position = "absolute";
        outgoing.style.inset = "0 auto auto 0";
        count.parentElement?.append(outgoing);
        plays.push(animate(outgoing, [{opacity:1}, {opacity:0}], RAIL_MOTION.exit, 0, false, RAIL_MOTION.leave).then(() => outgoing.remove()));
        plays.push(animate(count, [{opacity:0}, {opacity:1}], RAIL_MOTION.fadeIn, 0, false, RAIL_MOTION.enter));
      }
    }
    let distance = 0;
    for (const [key, part] of last) {
      if (key === "settled-section") continue;
      const old = first.get(key);
      if (old) distance = Math.max(distance, Math.abs(old.rect.top - part.rect.top));
    }
    const isRow = (key: string) => key.startsWith("row:") || key.startsWith("settled:") || key.startsWith("settled-message:");
    const rowArrival = [...last.keys()].some((key) => isRow(key) && !first.has(key));
    const rowExit = [...first.keys()].some((key) => isRow(key) && !last.has(key));
    const duration = rowExit || rowArrival ? RAIL_MOTION.space : railMoveDuration(distance);
    for (const [key, part] of last) {
      if (key === "settled-section") continue;
      const old = first.get(key);
      if (!old) {
        plays.push(animate(part.node, [{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "none" }],
          RAIL_MOTION.fadeIn, isRow(key) ? RAIL_MOTION.fadeDelay : 0, false, RAIL_MOTION.enter));
        continue;
      }
      // Children inherit their card's displacement. Subtract it to avoid double motion.
      const oldParent = part.parent ? first.get(part.parent) : null;
      const newParent = part.parent ? last.get(part.parent) : null;
      const parentY = oldParent && newParent ? oldParent.rect.top - newParent.rect.top : 0;
      const parentX = oldParent && newParent ? oldParent.rect.left - newParent.rect.left : 0;
      const y = old.rect.top - part.rect.top - parentY;
      const x = old.rect.left - part.rect.left - parentX;
      const travelling = key.startsWith("row:") && old.node.parentElement !== part.node.parentElement;
      if (Math.abs(y) > .5 || Math.abs(x) > .5) plays.push(animate(part.node,
        [{ transform: `translate(${x}px,${y}px)` }, { transform: "none" }], duration, 0, travelling));
      if (key.startsWith("row:") && old.color !== part.color)
        plays.push(animate(part.node, [{ color: old.color }, { color: part.color }], duration, 0, travelling));
      if (old.mark && part.mark && old.mark.join() !== part.mark.join()) {
        const mark = part.node.querySelector<HTMLElement>(".stage-mark--current-running");
        const colors = (value: string[]) => ({ borderTopColor:value[0], borderRightColor:value[1], borderBottomColor:value[2], borderLeftColor:value[3] });
        if (mark) plays.push(animate(mark, [colors(old.mark), colors(part.mark)], duration));
      }
    }
    await Promise.all(plays);
    busy = false;
    railRoot?.removeAttribute("data-rail-motion-busy");
    void drain();
  }
  function signalsOnly(next: WorkspaceRailPresentation): WorkspaceRailPresentation {
    const current = read();
    // Discovery deliberately drops a read message. Read its facts from all
    // tickets so a row held under the pointer still loses its unread mark.
    const tickets = new Map([...next.rail.groups.flatMap((group) => group.cards),
      ...[...next.rail.items, ...next.rail.settledItems].flatMap((item) => item.tickets)]
      .map((ticket) => [ticket.id, ticket]));
    return {...current, rail:refreshWorkspaceRailSignals(current.rail, next.rail),
      settledMessages:Object.fromEntries(Object.entries(current.settledMessages).map(([id, messages]) =>
        [id, messages.map((message) => {
          const ticket = tickets.get(message.id);
          return ticket ? {...message, conversationSignal:ticket.conversation_signal,
            conversationId:ticket.conversation_id ?? message.conversationId} : message;
        })]))};
  }
  return {
    update(next: WorkspaceRailPresentation) {
      if (disposed) return;
      pending = next;
      if (frozen) apply(signalsOnly(next));
      void drain();
    },
    freeze(value: boolean) {
      frozen = value;
      if (value && pending && !disposed) apply(signalsOnly(pending));
      if (!value) void drain();
    },
    dispose() { root()?.removeAttribute("data-rail-motion-busy"); disposed = true; pending = null; for (const animation of animations) animation.cancel(); animations.clear(); media?.removeEventListener("change", motionPreferenceChanged); }
  };
}
