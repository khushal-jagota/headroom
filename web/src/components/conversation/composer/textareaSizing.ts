import { COMPOSER_TEXTAREA_MAX_LINES } from "./config";

/** Size the border box while keeping every visible line whole. */
export function calculateComposerTextareaHeight({
  lineHeight,
  scrollHeight,
  verticalPadding,
  verticalBorder
}: {
  lineHeight: number;
  scrollHeight: number;
  verticalPadding: number;
  verticalBorder: number;
}): { height: number; overflowY: "auto" | "hidden" } {
  const chrome = verticalPadding + verticalBorder;
  // scrollHeight is integral even when a font's line height is fractional.
  const wantedLines = Math.max(1, Math.ceil((scrollHeight - verticalPadding - 1) / lineHeight));
  return {
    height: Math.min(wantedLines, COMPOSER_TEXTAREA_MAX_LINES) * lineHeight + chrome,
    overflowY: wantedLines > COMPOSER_TEXTAREA_MAX_LINES ? "auto" : "hidden"
  };
}
