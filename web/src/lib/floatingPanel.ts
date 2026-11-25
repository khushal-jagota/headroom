import { autoUpdate, computePosition, flip, hide, offset, shift, size, type Placement } from "@floating-ui/dom";
import type { Attachment } from "svelte/attachments";

export type { Placement as FloatingPanelPlacement };

const TRIGGER_GAP = 4;
const VIEWPORT_MARGIN = 8;

/** Float a menu or picker panel beside its trigger, the standard way.
 *
 * The panel carries `popover="manual"`, so it draws in the browser's top layer, in front
 * of everything on the page, the conversation dock included. Floating UI places it against
 * the window: on the preferred side when it fits, on the other side when it does not,
 * slid along the edge to stay on screen, and no taller than the room on its side. It stays
 * attached to the trigger while the page scrolls, and is hidden while the trigger is
 * scrolled out of view.
 *
 * Use it as `{@attach floatingPanel(trigger, "bottom-start")}` on an element that is only
 * mounted while the menu is open. `afterFirstPlacement` runs once, when the panel first has
 * its place and height, for work that needs them, such as scrolling the chosen row into view. */
export function floatingPanel(
  trigger: Element | null | undefined,
  placement: Placement = "bottom-start",
  afterFirstPlacement?: () => void
): Attachment<HTMLElement> {
  return (panel) => {
    if (!trigger) return;
    panel.showPopover();
    let placed = false;
    async function place(): Promise<void> {
      if (!trigger) return;
      // Measure the panel at its natural height, so the choice of side is not
      // decided by a cap from an earlier placement. Uncapped, a list inside has nothing
      // to scroll and loses its place, so note where each one was and put it back.
      const scrolled = [panel, ...panel.querySelectorAll<HTMLElement>("*")]
        .filter((element) => element.scrollTop > 0)
        .map((element) => [element, element.scrollTop] as const);
      panel.style.maxHeight = "";
      const { x, y, middlewareData } = await computePosition(trigger, panel, {
        strategy: "fixed",
        placement,
        middleware: [
          offset(TRIGGER_GAP),
          flip({ padding: VIEWPORT_MARGIN }),
          shift({ padding: VIEWPORT_MARGIN }),
          size({
            padding: VIEWPORT_MARGIN,
            apply({ availableHeight, elements }) {
              elements.floating.style.maxHeight = `${Math.max(0, availableHeight)}px`;
            }
          }),
          hide()
        ]
      });
      panel.style.left = `${x}px`;
      panel.style.top = `${y}px`;
      panel.style.visibility = middlewareData.hide?.referenceHidden ? "hidden" : "";
      for (const [element, top] of scrolled) element.scrollTop = top;
      if (!placed) {
        placed = true;
        afterFirstPlacement?.();
      }
    }
    const stop = autoUpdate(trigger, panel, () => void place());
    return () => {
      stop();
      if (panel.matches(":popover-open")) panel.hidePopover();
    };
  };
}
