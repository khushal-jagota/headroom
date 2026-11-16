import type { RegisterManagedArtifactFrame } from "../managedArtifactPress";
import { COMPACT_LAYOUT_MEDIA_QUERY } from "../compactLayout";
import { visualViewportClientRect } from "../keyboardViewport";

/** Dismiss on the press origin so a selection may finish outside the card. On a wide
 *  screen a press beside the pane is outside it. In the phone layout the pane fills the
 *  width, and the strips of layer around it are where a finger lands beside or under the
 *  composer: a phone moves that near-miss tap's focus onto the field while reporting the
 *  press where the finger landed, so there the whole layer is inside. */
export function isPrimaryPressOutsideConversation(event: PointerEvent): boolean {
  if (!event.isPrimary || event.button !== 0 || !(event.target instanceof Element)) return false;
  const inside = window.matchMedia(COMPACT_LAYOUT_MEDIA_QUERY).matches
    ? ".conversation-layer, [data-conversation-pane]" : "[data-conversation-pane]";
  return event.target.closest(inside) === null;
}

/** Reject frames outside the current visible, unclipped part of this surface. */
function visibleArtifactFrame(frame: HTMLIFrameElement, host: HTMLElement): boolean {
  if (document.visibilityState === "hidden" || !frame.isConnected || !host.contains(frame) || frame.closest("[inert], [data-conversation-pane]")) return false;
  if (!frame.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
  const rect = frame.getBoundingClientRect();
  const viewport = visualViewportClientRect() ?? { top: 0, left: 0, width: window.innerWidth, height: window.innerHeight };
  let left = Math.max(viewport.left, rect.left), top = Math.max(viewport.top, rect.top);
  let right = Math.min(viewport.left + viewport.width, rect.right);
  let bottom = Math.min(viewport.top + viewport.height, rect.bottom);
  for (let parent = frame.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    const bounds = parent.getBoundingClientRect();
    if (style.overflowX !== "visible") { left = Math.max(left, bounds.left); right = Math.min(right, bounds.right); }
    if (style.overflowY !== "visible") { top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom); }
  }
  return right > left && bottom > top;
}

/** One surface owns both outside pointer origins and its explicitly registered artifact. */
export function createConversationDismissal(dismiss: () => void) {
  let host: HTMLElement | null = null;
  const registrations = new Set<() => void>();
  const registerArtifactFrame: RegisterManagedArtifactFrame = frame => {
    let current: Document | null = null;
    function press(event: PointerEvent) {
      if (event.isPrimary && event.button === 0 && host && visibleArtifactFrame(frame, host)) dismiss();
    }
    function attach() {
      current?.removeEventListener("pointerdown", press, { capture: true });
      // Navigated pages can be cross-origin. The browser denies access to their document.
      current = frame.contentDocument;
      current?.addEventListener("pointerdown", press, { capture: true });
    }
    frame.addEventListener("load", attach);
    attach();
    function unregister() {
      frame.removeEventListener("load", attach);
      current?.removeEventListener("pointerdown", press, { capture: true });
      current = null;
      registrations.delete(unregister);
    }
    registrations.add(unregister);
    return unregister;
  };
  function outsidePress(event: PointerEvent): void {
    if (isPrimaryPressOutsideConversation(event)) dismiss();
  }
  function surface(element: HTMLElement) {
    host = element;
    element.addEventListener("pointerdown", outsidePress, true);
    return { destroy() {
      element.removeEventListener("pointerdown", outsidePress, true);
      host = null;
      for (const unregister of registrations) unregister();
    } };
  }
  return { surface, registerArtifactFrame };
}
