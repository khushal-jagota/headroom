import { describe, expect, it } from "vitest";
import { parse } from "postcss";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";

const read = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = parse(read("../../assets/app.css"));
const tokens = parse(read("../../assets/tokens.css"));
const values = new Map<string, string>();
tokens.walkDecls((declaration) => { values.set(declaration.prop, declaration.value); });
const resolve = (value: string): string => value.replace(/var\((--[\w-]+)\)/g, (_, name: string) => {
  const token = values.get(name);
  if (token === undefined) throw new Error(`Missing design token: ${name}`);
  return resolve(token);
});

// Inspect the declarations a selector owns, with its actual token values. Browser
// captures separately prove cascade, inheritance, and geometry across the full UI.
function declarations(selector: string, media?: string, stylesheet = app): Record<string, string> {
  const result: Record<string, string> = {};
  stylesheet.walkRules((rule) => {
    if (rule.selector !== selector) return;
    const parent = rule.parent;
    const condition = parent?.type === "atrule" ? parent.params : undefined;
    if (condition !== media) return;
    rule.walkDecls((declaration) => { result[declaration.prop] = resolve(declaration.value); });
  });
  return result;
}

const pane = "[data-conversation-pane]";
const markdown = `${pane} .chat-thread .markdown`;
const narration = `${pane}[data-conversation-lens="full"] :is(.chat-a--narration, .c2-streaming)`;
const inlineCode = `${pane} .chat-thread .markdown :not(pre) > code,\n${pane} .cv-code`;

describe("final conversation style contracts", () => {
  it("keeps answer prose, heading levels, and inline chips distinct from Full narration", () => {
    expect(declarations(`${pane} .chat-a .markdown`)).toMatchObject({
      "font-size": "15px", "line-height": "24px", color: "#ececec"
    });
    expect(declarations(`${pane} .chat-thread .chat-a .markdown :is(h1, h2)`)).toMatchObject({
      "font-size": "19px", "line-height": "26px", color: "#f2f2f2", "font-weight": "600"
    });
    expect(declarations(`${pane} .chat-thread .chat-a .markdown h3`)).toMatchObject({
      "font-size": "17px", "line-height": "24px", color: "#ececec"
    });
    expect(declarations(inlineCode)).toMatchObject({
      "font-size": "0.85em", padding: "1px 4px", "border-radius": "4px", background: "#232323"
    });
    expect(declarations(`${pane} .chat-thread .chat-a .markdown :is(h1, h2, h3)`)).toMatchObject({ margin: "24px 0 8px" });
    expect(declarations(`${pane} .chat-thread .chat-a .markdown > :is(h1, h2, h3):first-child`)).toMatchObject({ "margin-block-start": "0" });
    expect(declarations(`${pane} .chat-thread-content > [data-conversation-transcript] > [data-rhythm="meta"] .chat-a .markdown > :is(h1, h2, h3, h4, h5, h6):first-child`)).toMatchObject({ "margin-block-start": "16px" });
    expect(declarations('.chat-thread-content > [data-conversation-transcript] > [data-rhythm="turn"]')).toMatchObject({ "margin-block-start": "40px" });
    expect(declarations('.chat-thread-content > [data-conversation-transcript] > [data-rhythm="meta"]')).toMatchObject({ "margin-block-start": "8px" });
    expect(declarations(`${markdown} :is(p, ul, ol, blockquote)`)).toMatchObject({ "margin-block-end": "16px" });
    const userStyle = parse(read("../src/components/conversation/UserMessage.svelte").split("<style>")[1]!.split("</style>")[0]!);
    expect(declarations(".chat-u, .chat-system", undefined, userStyle)).toMatchObject({ gap: "12px" });
  });

  it("keeps Full narration flush, lighter, and its inline code unfilled", () => {
    expect(declarations(`${narration},\n${narration} .markdown`)).toMatchObject({
      "font-size": "14px", "line-height": "24px", color: "#c4c4c4", "text-wrap": "pretty"
    });
    expect(declarations(`${pane}[data-conversation-lens="full"] .chat-thread :is(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code`)).toMatchObject({
      "font-size": "0.9em", "line-height": "inherit", padding: "0", background: "none", color: "#dcdcdc"
    });
  });

  it("keeps links neutral and tables proportional with a stronger header seam", () => {
    expect(declarations(`${markdown} a:not(.file-preview-artifact-link, .pv-act)`)).toMatchObject({
      color: "#ececec", "text-decoration-color": "rgb(236 236 236 / 50%)"
    });
    expect(declarations(`${markdown} a:not(.file-preview-artifact-link, .pv-act):hover`)).toMatchObject({ color: "#ffffff" });
    expect(declarations(`${markdown} table`)).toMatchObject({ "font-variant-numeric": "normal" });
    expect(declarations(`${markdown} th`)).toMatchObject({
      color: "#ececec", "font-size": "14px", "line-height": "20px", "font-weight": "600", "border-block-end": "1px solid #3a3a3a"
    });
    expect(declarations(`${markdown} :is(th, td)`)).toMatchObject({ height: "40px" });
  });

  it("keeps Stop bright while an empty Send remains subdued", () => {
    expect(declarations(".chat-send")).toMatchObject({ width: "32px", height: "32px", background: "#2a2a2a", color: "#9a9a9a" });
    expect(declarations(".chat-send.stop")).toMatchObject({ background: "#ececec", "box-shadow": "none", color: "#141414" });
    expect(declarations(".chat-send.stop:hover")).toMatchObject({ background: "#ffffff" });
    expect(declarations(".chat-send.stop .chat-send-fill")).toMatchObject({ width: "10px", height: "10px", "border-radius": "2px" });
  });

  it("keeps chart colors distinct and media edges at the final contrast", () => {
    expect(declarations(`${pane} .chat-thread .chat-chart .goal-chart-host`)).toMatchObject({
      "--accent-bright": "#bccbf0", "--chart-compare-second": "#e0b36b"
    });
    expect(declarations(`${pane} .file-preview-media::after`)).toMatchObject({ "box-shadow": "inset 0 0 0 1px rgb(255 255 255 / 16%)" });
    expect(declarations(`${pane} .chat-thread .chat-chart .goal-chart-host .snapshot-legend`)).toMatchObject({ "padding-inline-start": "0" });
  });

  it("retains status-icon motion and its reduced-motion override", () => {
    const selector = `${pane} :is(.c2-meta-slot, .c2-status-slot, .c2-rest-activity > :is(.cv-spinner, .cv-dot, .cv-glyph))`;
    expect(declarations(selector)).toMatchObject({ animation: "cv-status-icon-in 150ms ease-out" });
    expect(declarations(selector, "(prefers-reduced-motion: reduce)")).toMatchObject({ animation: "none" });
  });
});
