import { tick } from "svelte";
import { refreshWorkspaceRailSignals, stabilizeWorkspaceRail, type WorkspaceRail } from "./workspaceRail";

// The approved Glide timing: distance changes duration, never the easing.
export const RAIL_MOTION = {
  moveMin: 360, moveMax: 560, space: 340,
  fadeIn: 320, fadeDelay: 140, exit: 160,
  ease: "cubic-bezier(.4,.02,.2,1)", enter: "cubic-bezier(.05,.7,.1,1)", leave: "cubic-bezier(.3,0,.8,.15)"
};
export const railMoveDuration = (distance: number) => Math.round(Math.max(RAIL_MOTION.moveMin,
  Math.min(RAIL_MOTION.moveMax, 300 + Math.abs(distance) * .7)));

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
  read: () => WorkspaceRail, apply: (rail: WorkspaceRail) => void) {
  let pending: WorkspaceRail | null = null;
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
    const next = stabilizeWorkspaceRail(pending, read()); pending = null;
    const first = snapshot(root());
    // Exit while the old space still exists. Headings leave with their last row.
    const remaining = new Set<string>(["card:chief", ...next.items.map((item) => `card:${item.id}`)]);
    for (const [id, groups] of [["chief", next.chiefGroups], ...next.items.map((item) => [item.id, item.groups] as const)] as const) {
      for (const group of groups) {
        remaining.add(`heading:${id}:${group.key}`);
        for (const ticket of group.cards) remaining.add(`row:${ticket.id}`);
      }
    }
    const exits = [...first].filter(([key]) => !remaining.has(key));
    await Promise.all(exits.map(([, part]) => animate(part.node, [{ opacity: 1 }, { opacity: 0 }], RAIL_MOTION.exit, 0, false, RAIL_MOTION.leave)));
    if (disposed) return;
    // A pointer can arrive during an exit. Restore it, and defer the whole structure.
    if (frozen) {
      pending ??= next;
      apply(refreshWorkspaceRailSignals(read(), pending));
      busy = false;
      return;
    }
    apply(next);
    await tick();
    if (disposed) return;
    const last = snapshot(root());
    const plays: Promise<void>[] = [];
    let distance = 0;
    for (const [key, part] of last) {
      const old = first.get(key);
      if (old) distance = Math.max(distance, Math.abs(old.rect.top - part.rect.top));
    }
    const rowArrival = [...last.keys()].some((key) => key.startsWith("row:") && !first.has(key));
    const rowExit = [...first.keys()].some((key) => key.startsWith("row:") && !last.has(key));
    const duration = rowExit || rowArrival ? RAIL_MOTION.space : railMoveDuration(distance);
    for (const [key, part] of last) {
      const old = first.get(key);
      if (!old) {
        plays.push(animate(part.node, [{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "none" }],
          RAIL_MOTION.fadeIn, key.startsWith("row:") ? RAIL_MOTION.fadeDelay : 0, false, RAIL_MOTION.enter));
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
    void drain();
  }
  return {
    update(next: WorkspaceRail) {
      if (disposed) return;
      pending = next;
      if (frozen) apply(refreshWorkspaceRailSignals(read(), next));
      void drain();
    },
    freeze(value: boolean) {
      frozen = value;
      if (value && pending && !disposed) apply(refreshWorkspaceRailSignals(read(), pending));
      if (!value) void drain();
    },
    dispose() { disposed = true; pending = null; for (const animation of animations) animation.cancel(); animations.clear(); media?.removeEventListener("change", motionPreferenceChanged); }
  };
}
