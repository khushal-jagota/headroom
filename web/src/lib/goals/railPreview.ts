import { railPreview, RAIL_TOUCH_MOVE_TOLERANCE } from '../workspaceRailInteraction';

export const GOAL_PREVIEW_WIDTH = 340;
export const GOAL_PREVIEW_GUTTER = 12;
export const GOAL_PREVIEW_RAIL_GAP = 8;
export const GOAL_PREVIEW_ANCHOR_OFFSET = 0;

/** Commit native drafts while their controls are still mounted. */
export function blurGoalPreviewInput(panel: HTMLElement | null): void {
  const focused = document.activeElement;
  if (focused instanceof HTMLElement && panel?.contains(focused) && focused.matches('input,textarea,[contenteditable="true"]')) focused.blur();
}

export function goalPreviewPosition(anchor: DOMRect, railRight: number, width: number, height: number, viewportWidth: number, viewportHeight: number) {
  return {
    left: Math.max(GOAL_PREVIEW_GUTTER, Math.min(railRight + GOAL_PREVIEW_RAIL_GAP, viewportWidth - width - GOAL_PREVIEW_GUTTER)),
    top: Math.max(GOAL_PREVIEW_GUTTER, Math.min(anchor.top - GOAL_PREVIEW_ANCHOR_OFFSET, viewportHeight - height - GOAL_PREVIEW_GUTTER))
  };
}

type RowOptions = {show:(target:HTMLElement,immediate:boolean)=>void;leave:(target:HTMLElement)=>void};

/** The shared input owns timing and keeps a completed touch hold through its release.
 * This boundary also swallows that release's click when the browser retargets it from
 * the row into the panel that now overlaps it. */
export function createGoalRailPreviewInput() {
  let releaseClick: HTMLElement | null = null;
  return {
    clearTouch() { releaseClick = null; },
    click(event: MouseEvent) {
      if (!releaseClick) return;
      releaseClick = null;
      event.preventDefault();
      event.stopImmediatePropagation();
    },
    row(node: HTMLElement, options: RowOptions) {
      let current = options;
      let touchStart: {x:number;y:number;id:number} | null = null;
      const down = (event: PointerEvent) => {
        releaseClick = null;
        touchStart = event.pointerType === 'touch' ? {x:event.clientX,y:event.clientY,id:event.pointerId} : null;
      };
      const move = (event: PointerEvent) => {
        if (touchStart && (event.pointerId !== touchStart.id || Math.hypot(event.clientX-touchStart.x,event.clientY-touchStart.y) > RAIL_TOUCH_MOVE_TOLERANCE)) {
          touchStart = null; releaseClick = null;
        }
      };
      const end = () => { touchStart = null; };
      const cancel = () => { touchStart = null; releaseClick = null; };
      const listeners: [string, EventListener][] = [
        ['pointerdown',down as EventListener],['pointermove',move as EventListener],['pointerup',end],['pointercancel',cancel]
      ];
      for (const [name, listener] of listeners) node.addEventListener(name,listener,true);
      const wrapped = (): NonNullable<Parameters<typeof railPreview>[1]> => ({
        show(target,immediate) {
          if (immediate && touchStart) releaseClick=node;
          current.show(target,immediate);
        },
        leave() { current.leave(node); }
      });
      const action=railPreview(node,wrapped());
      return {
        update(next: RowOptions) { current=next; action.update(wrapped()); },
        destroy() {
          for (const [name, listener] of listeners) node.removeEventListener(name,listener,true);
          if (releaseClick===node) releaseClick=null;
          action.destroy();
        }
      };
    }
  };
}
