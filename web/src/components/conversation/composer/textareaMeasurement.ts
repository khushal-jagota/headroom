import { calculateComposerTextareaHeight } from "./textareaSizing";

// Preserve the textarea's text layout, including platform scrollbar geometry. The
// same classes also retain any native scrollbar pseudo-element rules.
const measurementProperties = [
  "width", "box-sizing", "font-family", "font-size", "font-weight", "font-style",
  "font-stretch", "font-variant", "font-size-adjust", "font-synthesis",
  "font-feature-settings", "font-variation-settings",
  "font-kerning", "font-optical-sizing", "line-height", "letter-spacing", "word-spacing",
  "text-indent", "text-transform", "text-rendering", "text-size-adjust", "tab-size",
  "white-space", "overflow-wrap", "word-break", "hyphens", "line-break",
  "text-align", "direction", "writing-mode", "overflow-x",
  "text-orientation", "padding-top", "padding-right", "padding-bottom", "padding-left",
  "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
  "border-top-style", "border-right-style", "border-bottom-style", "border-left-style",
  "scrollbar-width", "scrollbar-gutter", "appearance"
] as const;

/** Measure a draft without changing the real textarea or its scroll container. */
export function createComposerTextareaMeasurement(input: HTMLTextAreaElement) {
  const mirror = input.ownerDocument.createElement("textarea");
  mirror.setAttribute("data-composer-measurement", "");
  mirror.setAttribute("aria-hidden", "true");
  mirror.tabIndex = -1;
  mirror.inert = true;
  mirror.rows = 1;
  mirror.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;"
    + "top:0;left:0;display:block;margin:0;min-width:0;max-width:none;"
    + "min-height:0;max-height:none;resize:none;"
    + "transition:none;animation:none;";
  input.after(mirror);

  return {
    input,
    measure(style: CSSStyleDeclaration, paneHeight: number) {
      mirror.className = input.className;
      mirror.wrap = input.wrap;
      for (const property of measurementProperties) {
        mirror.style.setProperty(property, style.getPropertyValue(property));
      }
      // A native textarea counts trailing newlines itself. No sentinel character
      // changes wrapping at the right edge of a full line.
      mirror.value = input.value;
      mirror.style.height = "0px";
      mirror.style.overflowY = "hidden";
      const verticalPadding = Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom);
      const verticalBorder = Number.parseFloat(style.borderTopWidth) + Number.parseFloat(style.borderBottomWidth);
      const chrome = verticalPadding + verticalBorder;
      const contentBox = style.boxSizing !== "border-box";
      const calculate = () => calculateComposerTextareaHeight({
        lineHeight: Number.parseFloat(style.lineHeight), paneHeight,
        scrollHeight: mirror.scrollHeight, verticalPadding, verticalBorder
      });
      let size = calculate();
      if (size.overflowY === "auto") {
        // A capped textarea can acquire a scrollbar and wrap more narrowly. Read
        // that final width on the mirror, never by toggling the live control.
        mirror.style.height = `${size.height - (contentBox ? chrome : 0)}px`;
        mirror.style.overflowY = "auto";
        size = calculate();
      }
      return { ...size, height: size.height - (contentBox ? chrome : 0) };
    },
    dispose() {
      mirror.remove();
    }
  };
}
