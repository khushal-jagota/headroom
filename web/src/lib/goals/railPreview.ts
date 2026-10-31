import { railPreview, RAIL_TOUCH_MOVE_TOLERANCE } from '../workspaceRailInteraction';

export const GOAL_PREVIEW_WIDTH = 340;
export const GOAL_PREVIEW_GUTTER = 12;
export const GOAL_PREVIEW_RAIL_GAP = 14;
export const GOAL_PREVIEW_ANCHOR_OFFSET = 8;

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

/** The shared input owns timing. This boundary preserves successful touch releases,
 * including a release click retargeted from the row into its overlapping panel. */
export function createGoalRailPreviewInput(active: () => HTMLElement | null) {
  let heldTarget: HTMLElement | null = null;
  let releaseClick = false;
  return {
    clearTouch() { heldTarget = null; releaseClick = false; },
    click(event: MouseEvent) {
      if (!releaseClick) return;
      releaseClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    },
    row(node: HTMLElement, options: RowOptions) {
      let current = options;
      let touchStart: {x:number;y:number;id:number} | null = null;
      const down = (event: PointerEvent) => {
        heldTarget = null; releaseClick = false;
        touchStart = event.pointerType === 'touch' ? {x:event.clientX,y:event.clientY,id:event.pointerId} : null;
      };
      const move = (event: PointerEvent) => {
        if (touchStart && (event.pointerId !== touchStart.id || Math.hypot(event.clientX-touchStart.x,event.clientY-touchStart.y) > RAIL_TOUCH_MOVE_TOLERANCE)) {
          touchStart = null; heldTarget = null; releaseClick = false;
        }
      };
      const end = () => { touchStart = null; };
      const cancel = () => { touchStart = null; heldTarget = null; releaseClick = false; };
      const leave = (event: PointerEvent) => {
        if (event.pointerType === 'touch' && heldTarget === node && active() === node) event.stopImmediatePropagation();
      };
      const blur = (event: FocusEvent) => {
        if (heldTarget === node && active() === node && (event.relatedTarget === null || event.relatedTarget === document.body)) event.stopImmediatePropagation();
      };
      const listeners: [string, EventListener][] = [
        ['pointerdown',down as EventListener],['pointermove',move as EventListener],['pointerup',end],
        ['pointercancel',cancel],['pointerleave',leave as EventListener],['blur',blur as EventListener]
      ];
      for (const [name, listener] of listeners) node.addEventListener(name,listener,true);
      const wrapped = (): NonNullable<Parameters<typeof railPreview>[1]> => ({
        show(target,immediate) {
          if (immediate && touchStart) { heldTarget=node; releaseClick=true; }
          current.show(target,immediate);
        },
        leave() { current.leave(node); }
      });
      const action=railPreview(node,wrapped());
      return {
        update(next: RowOptions) { current=next; action.update(wrapped()); },
        destroy() {
          for (const [name, listener] of listeners) node.removeEventListener(name,listener,true);
          if (heldTarget===node) { heldTarget=null; releaseClick=false; }
          action.destroy();
        }
      };
    }
  };
}
