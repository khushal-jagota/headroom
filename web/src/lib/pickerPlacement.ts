/** Where a floating picker panel sits so it stays on screen.
 *
 * Coordinates are viewport pixels. A composer covering the bottom of the page
 * is a limit: the panel keeps a gap above it and flips up when the preferred
 * side does not fit.
 */

export const PICKER_VIEWPORT_MARGIN = 8;
export const PICKER_TRIGGER_GAP = 4;
export const PICKER_COMPOSER_GAP = 8;

export type PickerPlacement = {
  left: number;
  top: number;
  height: number;
  down: boolean;
};

export function placePickerPanel(input: {
  trigger: { top: number; bottom: number; left: number };
  panel: { width: number; height: number };
  viewport: { width: number; height: number };
  /** Top of a visible composer, or null when none is drawn. */
  composerTop: number | null;
  preferBelow: boolean;
}): PickerPlacement | null {
  const { trigger, panel, viewport, composerTop, preferBelow } = input;
  const topLimit = PICKER_VIEWPORT_MARGIN;
  const bottomLimit = Math.min(
    viewport.height - PICKER_VIEWPORT_MARGIN,
    composerTop === null ? viewport.height - PICKER_VIEWPORT_MARGIN : composerTop - PICKER_COMPOSER_GAP
  );
  if (trigger.bottom <= topLimit || trigger.top >= bottomLimit) return null;
  const above = Math.max(0, trigger.top - PICKER_TRIGGER_GAP - topLimit);
  const beneath = Math.max(0, bottomLimit - trigger.bottom - PICKER_TRIGGER_GAP);
  const fitsAbove = panel.height <= above;
  const fitsBelow = panel.height <= beneath;
  const down = fitsAbove && fitsBelow
    ? preferBelow
    : fitsBelow
      ? true
      : fitsAbove
        ? false
        : beneath >= above;
  const room = down ? beneath : above;
  if (room <= 0) return null;
  const height = Math.min(panel.height, room);
  const top = down ? trigger.bottom + PICKER_TRIGGER_GAP : trigger.top - PICKER_TRIGGER_GAP - height;
  const left = Math.max(
    PICKER_VIEWPORT_MARGIN,
    Math.min(trigger.left, viewport.width - PICKER_VIEWPORT_MARGIN - panel.width)
  );
  return { left, top, height, down };
}
