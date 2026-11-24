import { createRawSnippet } from "svelte";
import { render } from "svelte/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import WindowPageRow from "../src/components/desktopWindow/WindowPageRow.svelte";
import WindowTopRow from "../src/components/desktopWindow/WindowTopRow.svelte";
import { foldedActionCount } from "../src/lib/desktopWindow/pageRowFold";
import { keyTogglesWindowSidebar, liftPageRow, shownPageRow } from "../src/lib/desktopWindow/windowState.svelte";

const band = createRawSnippet(() => ({ render: () => `<div class="page-band">Panels · Ask for a recap</div>` }));

function insideTheDesktopWindow(): void {
  vi.stubGlobal("document", { documentElement: { hasAttribute: (name: string) => name === "data-desktop-window" } });
}

describe("the desktop window's top row", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws nothing in a browser, and the page keeps its own top row where it stands", () => {
    expect(render(WindowTopRow, { props: { reviewCount: 2 } }).body).not.toContain("window-");
    expect(render(WindowPageRow, { props: { children: band } }).body).toContain(`<div class="page-band">`);
  });

  it("in the desktop window, draws the sidebar button, and lifts the page's row out of the page", () => {
    insideTheDesktopWindow();
    const top = render(WindowTopRow, { props: { reviewCount: 2 } }).body;
    expect(top).toContain(`aria-label="Hide sidebar"`);
    expect(render(WindowPageRow, { props: { children: band } }).body).not.toContain("page-band");
  });

  it("shows the newest page's row, and gives the row back when that page leaves", () => {
    const first = createRawSnippet(() => ({ render: () => "<div>first</div>" }));
    const second = createRawSnippet(() => ({ render: () => "<div>second</div>" }));
    const releaseFirst = liftPageRow(first);
    const releaseSecond = liftPageRow(second);
    expect(shownPageRow()).toBe(second);
    releaseSecond();
    expect(shownPageRow()).toBe(first);
    releaseFirst();
    expect(shownPageRow()).toBeNull();
  });

  it("hides and shows the sidebar on ⌃⌘S only", () => {
    const key = (init: Partial<KeyboardEvent>) => keyTogglesWindowSidebar({ key: "s", ctrlKey: true, metaKey: true, altKey: false, shiftKey: false, repeat: false, defaultPrevented: false, ...init } as KeyboardEvent);
    expect(key({})).toBe(true);
    expect(key({ key: "S" })).toBe(true);
    expect(key({ ctrlKey: false })).toBe(false);
    expect(key({ metaKey: false })).toBe(false);
    expect(key({ shiftKey: true })).toBe(false);
    expect(key({ repeat: true })).toBe(false);
    expect(key({ key: "\\" })).toBe(false);
  });
});

describe("a narrow page row", () => {
  // A context line 300 wide, three actions 100 wide, the "•••" button 30 wide, 12 between parts.
  const row = (rowWidth: number) => foldedActionCount({
    rowWidth, contextWidth: 300, contextMinWidth: 140, actionWidths: [100, 100, 100], actionGap: 0, rowGap: 12, moreWidth: 30
  });

  it("shortens the context line to 140 before any action folds", () => {
    expect(row(140 + 12 + 300)).toBe(0);
  });

  it("then folds the actions, last first, making room for the menu button", () => {
    expect(row(140 + 12 + 300 - 1)).toBe(1);
    expect(row(140 + 12 + 200 + 30)).toBe(1);
    expect(row(140 + 12 + 200 + 30 - 1)).toBe(2);
    expect(row(100)).toBe(3);
  });

  it("keeps a context line shorter than the floor at its own width", () => {
    expect(foldedActionCount({ rowWidth: 80 + 12 + 300, contextWidth: 80, contextMinWidth: 140, actionWidths: [100, 100, 100], actionGap: 0, rowGap: 12, moreWidth: 30 })).toBe(0);
  });
});
