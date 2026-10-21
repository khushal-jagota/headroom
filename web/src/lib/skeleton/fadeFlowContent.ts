/** Fade painted flow roots without adding a box to their flex/grid layout.
 * Flow snippets provide element roots; raw text belongs to an inline region.
 */
export function fadeFlowContent(node: HTMLElement, active: boolean) {
  let observer: MutationObserver | undefined;
  const owned = new Map<Element, Animation>();
  const existingEntrances = new Set<Element>();

  function stop() {
    observer?.disconnect();
    observer = undefined;
    owned.forEach(animation => animation.cancel());
    owned.clear();
    existingEntrances.clear();
  }

  function start() {
    // This animation keeps the shared token, easing and completion event on the
    // transparent wrapper. Its painted roots follow the same browser timeline.
    const wrapperStyle = getComputedStyle(node);
    const entrance = node.getAnimations().find(animation =>
      animation instanceof CSSAnimation && animation.animationName === "skeleton-in");
    if (!entrance?.effect) return;
    const timing = { ...entrance.effect.getTiming(), easing: wrapperStyle.animationTimingFunction };
    function visit(parent: Element) {
      for (const target of parent.children) {
        const style = getComputedStyle(target);
        if (style.display === "contents") { visit(target); continue; }
        if (owned.has(target) || existingEntrances.has(target)) continue;
        // Keep an existing entrance, including its opacity cadence and transform.
        // Adding another would reveal its unfinished opacity when ours ended.
        if (target.getAnimations().some(animation => animation.effect instanceof KeyframeEffect &&
          animation.effect.getKeyframes().some(frame => "opacity" in frame))) {
          existingEntrances.add(target);
          continue;
        }
        const animation = target.animate([{ opacity: 0 }, { opacity: style.opacity }], timing);
        animation.currentTime = entrance?.currentTime ?? 0;
        owned.set(target, animation);
      }
    }
    visit(node);
    // Markdown paints inside a display:contents host after insertion. Observe
    // child additions only until the shared entrance ends; never move its nodes.
    observer = new MutationObserver(() => visit(node));
    observer.observe(node, { childList: true, subtree: true });
  }

  if (active) start();
  return {
    update(next: boolean) {
      stop();
      if (next) start();
    },
    destroy: stop
  };
}
