import {
  COMPOSER_TEXTAREA_MAX_LINES,
  COMPOSER_TEXTAREA_MAX_PANE_FRACTION
} from "./config";

/** Size the border box while keeping every visible line whole. */
export function calculateComposerTextareaHeight({
  lineHeight,
  paneHeight,
  scrollHeight,
  verticalPadding,
  verticalBorder
}: {
  lineHeight: number;
  paneHeight: number;
  scrollHeight: number;
  verticalPadding: number;
  verticalBorder: number;
}): { height: number; overflowY: "auto" | "hidden" } {
  const chrome = verticalPadding + verticalBorder;
  const available = Math.min(
    paneHeight * COMPOSER_TEXTAREA_MAX_PANE_FRACTION,
    lineHeight * COMPOSER_TEXTAREA_MAX_LINES + chrome
  );
  const capLines = Math.max(1, Math.floor((available - chrome) / lineHeight));
  // scrollHeight is integral even when a font's line height is fractional.
  const wantedLines = Math.max(1, Math.ceil((scrollHeight - verticalPadding - 1) / lineHeight));
  return {
    height: Math.min(wantedLines, capLines) * lineHeight + chrome,
    overflowY: wantedLines > capLines ? "auto" : "hidden"
  };
}
