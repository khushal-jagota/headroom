import { MANAGED_ARTIFACT_PRIMARY_PRESS, type RegisterManagedArtifactFrame } from "../managedArtifactPress";

/** Dismiss on the press origin so a selection may finish outside the card. */
export function isPrimaryPressOutsideConversation(event: PointerEvent): boolean {
  return event.isPrimary && event.button === 0 && event.target instanceof Element &&
    event.target.closest("[data-conversation-pane]") === null;
}

/** Reject frames outside the current visible, unclipped part of this surface. */
function visibleArtifactFrame(frame: HTMLIFrameElement, host: HTMLElement): boolean {
  if (document.visibilityState === "hidden" || !frame.isConnected || !host.contains(frame) || frame.closest("[inert], [data-conversation-pane]")) return false;
  if (!frame.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
  const rect = frame.getBoundingClientRect();
  const viewport = window.visualViewport;
  const viewportLeft = viewport?.offsetLeft ?? 0, viewportTop = viewport?.offsetTop ?? 0;
  let left = Math.max(viewportLeft, rect.left), top = Math.max(viewportTop, rect.top);
  let right = Math.min(viewportLeft + (viewport?.width ?? window.innerWidth), rect.right);
  let bottom = Math.min(viewportTop + (viewport?.height ?? window.innerHeight), rect.bottom);
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
  const frames = new Set<HTMLIFrameElement>();
  const registerArtifactFrame: RegisterManagedArtifactFrame = frame => {
    frames.add(frame);
    return () => frames.delete(frame);
  };
  function outsidePress(event: PointerEvent): void {
    if (isPrimaryPressOutsideConversation(event)) dismiss();
  }
  function artifactPress(event: MessageEvent): void {
    if (event.data !== MANAGED_ARTIFACT_PRIMARY_PRESS || !host) return;
    for (const frame of frames) {
      if (event.source === frame.contentWindow && visibleArtifactFrame(frame, host)) {
        dismiss();
        return;
      }
    }
  }
  function surface(element: HTMLElement) {
    host = element;
    element.addEventListener("pointerdown", outsidePress, true);
    window.addEventListener("message", artifactPress);
    return { destroy() {
      element.removeEventListener("pointerdown", outsidePress, true);
      window.removeEventListener("message", artifactPress);
      host = null;
      frames.clear();
    } };
  }
  return { surface, registerArtifactFrame };
}
