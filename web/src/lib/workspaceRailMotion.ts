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

/** Rendered structure travels together, including independently discovered messages. */
export type WorkspaceRailPresentation = {
  rail: WorkspaceRail;
  settledOpen: boolean;
  /** Keep the closing body mounted while the header acknowledges the click. */
  settledBodyOpen?: boolean;
};

type MotionSurface = { background: string; surrounding: string };
type Part = { surface: MotionSurface; node: HTMLElement; rect: DOMRect; color: string; mark: string[] | null; parent: string | null };
function snapshot(root: HTMLElement | null): Map<string, Part> {
  if (!root || typeof document === "undefined") return new Map();
  // Resolve the complete batch before motion classes alter any ancestor surface.
  // Canvas composites transparent CSS colors over their real ancestor backgrounds.
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true })!;
  const backgrounds = new Map<HTMLElement, string>();
  function background(node: HTMLElement | null): string {
    if (!node) return "rgb(255, 255, 255)";
    const cached = backgrounds.get(node);
    if (cached) return cached;
    const behind = background(node.parentElement);
    context.fillStyle = behind;
    context.fillRect(0, 0, 1, 1);
    context.fillStyle = getComputedStyle(node).backgroundColor;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
    const color = `rgb(${r}, ${g}, ${b})`;
    backgrounds.set(node, color);
    return color;
  }
  return new Map(Array.from(root?.querySelectorAll<HTMLElement>("[data-rail-motion]") ?? []).map((node) => {
    const mark = node.dataset.railMotion?.startsWith("row:") ? node.querySelector<HTMLElement>(".stage-mark--current-running") : null;
    const style = mark && getComputedStyle(mark);
    return [node.dataset.railMotion!, { node, surface: { background: background(node), surrounding: background(node.parentElement) }, rect: node.getBoundingClientRect(), color: getComputedStyle(node).color,
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
  let manualOpen: boolean | null = null;
  let foldBodyOpen: boolean | null = null;
  let foldGeneration = 0;
  let foldBusy = false;
  // A coherent settled page can arrive while an unrelated Worker moves. Keep
  // that page live, but leave Settle/Reopen transitions in the shared rail lane.
  let directSettled: WorkspaceRailPresentation | null = null;
  // Scope withdrawal removes rows from the next publication before their
  // closing fade ends. Keep that body's membership, but refresh its signals.
  let closingBody: WorkspaceRailPresentation | null = null;
  const foldAnimations = new Set<Animation>();
  const activeParents = (value: WorkspaceRailPresentation) => value.rail.items.map(item => item.id).sort().join("\0");
  function withFold(value: WorkspaceRailPresentation): WorkspaceRailPresentation {
    if (manualOpen === null) return value;
    const direct = directSettled && activeParents(value) === activeParents(directSettled) ? directSettled : null;
    const settled = closingBody ?? direct;
    return {...value, settledOpen:manualOpen, settledBodyOpen:foldBodyOpen ?? manualOpen,
      ...(settled ? {rail:{...value.rail, settledItems:settled.rail.settledItems}} : {})};
  }
  const present = (value: WorkspaceRailPresentation) => apply(withFold(value));
  function markBusy() {
    if (busy || foldBusy || sectionBusy) root()?.setAttribute("data-rail-motion-busy", "");
    else root()?.removeAttribute("data-rail-motion-busy");
  }
  const animations = new Set<Animation>();
  let surfaces = new Map<HTMLElement, MotionSurface>();
  let sectionGeneration = 0;
  let sectionBusy = false;
  let sectionTarget: number | null = null;
  // Every publisher shares the section height. Hold its current visual size
  // until Svelte commits, then measure without a stale WAAPI height effect.
  function holdSection() {
    const section = root()?.querySelector<HTMLElement>(".settled");
    if (!section) return null;
    const generation = ++sectionGeneration;
    const height = section.getBoundingClientRect().height;
    section.style.height = `${height}px`;
    section.style.overflow = "hidden";
    return {section, generation, height};
  }
  async function resizeSection(held: ReturnType<typeof holdSection>, closing = false): Promise<void> {
    if (!held || disposed || held.generation !== sectionGeneration) return;
    const {section, generation, height} = held;
    const effects = [...animations].filter(animation =>
      (animation.effect as KeyframeEffect)?.target === section).map(animation => ({animation, effect:animation.effect}));
    for (const {animation} of effects) animation.effect = null;
    section.style.removeProperty("height");
    const target = closing
      ? section.querySelector<HTMLElement>(".settled-head")?.getBoundingClientRect().height ?? 0
      : section.getBoundingClientRect().height;
    for (const {animation, effect} of effects) animation.effect = effect;
    // Facts changing without geometry must not restart the existing beat.
    if (effects.length && sectionTarget !== null && Math.abs(target - sectionTarget) <= .5) {
      await Promise.all(effects.map(({animation}) => animation.finished.catch(() => {})));
    } else {
      for (const {animation} of effects) animation.cancel();
      sectionTarget = target;
      sectionBusy = Math.abs(target - height) > .5;
      markBusy();
      if (sectionBusy) await animate(section, [{height:`${height}px`}, {height:`${target}px`}], RAIL_MOTION.space);
    }
    if (disposed || generation !== sectionGeneration) return;
    sectionTarget = null;
    sectionBusy = false;
    section.style.removeProperty("height");
    section.style.removeProperty("overflow");
    markBusy();
  }
  function measure() {
    const parts = snapshot(root());
    surfaces = new Map([...parts.values()].map((part) => [part.node, part.surface]));
    return parts;
  }
  let media: MediaQueryList | null = null;
  const motionPreferenceChanged = () => { if (media?.matches) for (const animation of animations) animation.cancel(); };
  const reduced = () => {
    if (!media) {
      media = window.matchMedia("(prefers-reduced-motion: reduce)");
      media.addEventListener("change", motionPreferenceChanged);
    }
    return media.matches;
  };
  function animate(node: HTMLElement, frames: Keyframe[], duration: number, delay = 0, travelling = false, easing = RAIL_MOTION.ease, fold = false): Promise<void> {
    if (reduced() || typeof node.animate !== "function") return Promise.resolve();
    const surface = surfaces.get(node);
    if (surface) {
      node.style.setProperty("--rail-motion-background", surface.background);
      node.style.setProperty("--rail-motion-surrounding", surface.surrounding);
    }
    node.classList.add("rail-moving");
    const travellingCard = travelling ? node.closest<HTMLElement>(".manager-card") : null;
    if (travelling) { node.classList.add("rail-travelling"); travellingCard?.classList.add("rail-travelling-card"); }
    const animation = node.animate(frames, { duration, delay, easing, fill: "backwards" });
    animations.add(animation);
    if (fold) foldAnimations.add(animation);
    return animation.finished.catch(() => {}).then(() => {
      animations.delete(animation);
      foldAnimations.delete(animation);
      if (!Array.from(animations).some((active) => (active.effect as KeyframeEffect)?.target === node)) {
        node.classList.remove("rail-moving", "rail-travelling");
        node.style.removeProperty("--rail-motion-background");
        node.style.removeProperty("--rail-motion-surrounding");
      }
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
    const next = withFold({...pending, rail: stabilizeWorkspaceRail(pending.rail, previous.rail)}); pending = null;
    const directFold = () => manualOpen !== null && activeParents(previous) === activeParents(next);
    const railRoot = root();
    markBusy();
    const section = railRoot?.querySelector<HTMLElement>(".settled");
    const oldCount = railRoot?.querySelector<HTMLElement>("[data-settled-count]")?.textContent;
    const first = measure();
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

    }
    const settledPart = (key: string) => key.startsWith("settled:");
    const exits = [...first].filter(([key]) => !remaining.has(key) && !(directFold() && settledPart(key)));
    await Promise.all(exits.map(([, part]) => animate(part.node, [{ opacity: 1 }, { opacity: 0 }], RAIL_MOTION.exit, 0, false, RAIL_MOTION.leave)));
    if (disposed) return;
    // A pointer can arrive during an exit. Restore it, and defer the whole structure.
    if (frozen) {
      pending ??= next;
      present(signalsOnly(pending));
      busy = false;
      markBusy();
      return;
    }
    const heldSection = !directFold() ? holdSection() : null;
    present(next);
    await tick();
    if (disposed) return;
    const last = measure();
    const plays: Promise<void>[] = [];
    if (section && !directFold()) {
      plays.push(resizeSection(heldSection, manualOpen === false));
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
      if (key === "settled-section" || (directFold() && settledPart(key))) continue;
      const old = first.get(key);
      if (old) distance = Math.max(distance, Math.abs(old.rect.top - part.rect.top));
    }
    const isRow = (key: string) => key.startsWith("row:") || key.startsWith("settled:");
    const rowArrival = [...last.keys()].some((key) => isRow(key) && !first.has(key) && !(directFold() && settledPart(key)));
    const rowExit = [...first.keys()].some((key) => isRow(key) && !last.has(key) && !(directFold() && settledPart(key)));
    const duration = rowExit || rowArrival ? RAIL_MOTION.space : railMoveDuration(distance);
    for (const [key, part] of last) {
      if (key === "settled-section" || (directFold() && settledPart(key))) continue;
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
    markBusy();
    void drain();
  }
  function signalsOnly(next: WorkspaceRailPresentation): WorkspaceRailPresentation {
    const current = read();
    return {...current, rail:refreshWorkspaceRailSignals(current.rail, next.rail)};
  }

  async function setSettledOpen(open: boolean): Promise<void> {
    if (disposed || (manualOpen ?? read().settledOpen) === open) return;
    const generation = ++foldGeneration;
    const section = root()?.querySelector<HTMLElement>(".settled");
    const heldSection = holdSection();
    const chevron = section?.querySelector<HTMLElement>(".settled-chevron");
    const oldRotation = chevron ? getComputedStyle(chevron).transform : "none";
    const oldBody = section?.querySelector<HTMLElement>("[data-settled-scroll]");
    const oldOpacity = oldBody ? getComputedStyle(oldBody).opacity : "0";
    if (!open && oldBody?.contains(document.activeElement)) section?.querySelector<HTMLButtonElement>(".settled-head")?.focus();
    // The fold takes its own surfaces from any prior section resize, while
    // unrelated Worker movement keeps its existing animation.
    for (const animation of animations) {
      const target = (animation.effect as KeyframeEffect)?.target;
      if (foldAnimations.has(animation) || target === section || target === chevron || target === oldBody) animation.cancel();
    }
    foldAnimations.clear();
    closingBody = !open && oldBody ? read() : null;
    // A reversal keeps the visible body until a new coherent page arrives.
    // A closed-scope publication cannot supply its reopened membership.
    directSettled = null;
    manualOpen = open;
    foldBodyOpen = open || Boolean(oldBody);
    foldBusy = true;
    markBusy();
    present(read());
    await tick();
    if (disposed || generation !== foldGeneration) return;
    const plays: Promise<void>[] = [];
    if (section) {
      plays.push(resizeSection(heldSection, !open));
      if (chevron) plays.push(animate(chevron,
        [{transform:oldRotation}, {transform:`rotate(${open ? 180 : 0}deg)`}], RAIL_MOTION.space, 0, false, RAIL_MOTION.ease, true));
      const body = section.querySelector<HTMLElement>("[data-settled-scroll]");
      if (body) plays.push(animate(body, [{opacity:oldOpacity}, {opacity:open ? 1 : 0}],
        RAIL_MOTION.space, 0, false, open ? RAIL_MOTION.enter : RAIL_MOTION.leave, true));
    }
    await Promise.all(plays);
    if (disposed || generation !== foldGeneration) return;
    foldBodyOpen = open;
    closingBody = null;
    foldBusy = false;
    present(read());
    markBusy();
  }
  return {
    // A manual fold never waits for unrelated rail motion or a row hover.
    setSettledOpen(open: boolean) { void setSettledOpen(open); },
    update(next: WorkspaceRailPresentation) {
      if (disposed) return;
      pending = next;
      if (manualOpen !== null) {
        directSettled = activeParents(read()) === activeParents(next) ? next : null;
        if (closingBody) closingBody = signalsOnly(next);
        const heldSection = manualOpen && !closingBody && directSettled ? holdSection() : null;
        present(frozen ? signalsOnly(next) : read());
        if (heldSection) void tick().then(() => resizeSection(heldSection));
      } else if (frozen) present(signalsOnly(next));
      void drain();
    },
    freeze(value: boolean) {
      frozen = value;
      if (value && pending && !disposed) present(signalsOnly(pending));
      if (!value) void drain();
    },
    dispose() { ++sectionGeneration; root()?.querySelector<HTMLElement>(".settled")?.style.removeProperty("height"); root()?.removeAttribute("data-rail-motion-busy"); root()?.querySelector<HTMLElement>(".settled")?.style.removeProperty("overflow"); disposed = true; ++foldGeneration; pending = null; for (const animation of animations) animation.cancel(); animations.clear(); surfaces.clear(); media?.removeEventListener("change", motionPreferenceChanged); }
  };
}
