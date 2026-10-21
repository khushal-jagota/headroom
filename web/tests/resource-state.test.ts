/** What a screen shows when a read fails under it.
 *
 * ResourceState owns the three states every screen has. The rule it must keep is
 * that a screen holding data never loses it: a failed read is a line above the
 * data, not a replacement for the screen. A screen with nothing to show still
 * gives itself over to the error or reserves space during the initial wait.
 */

import { createRawSnippet } from "svelte";
import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import ResourceState from "../src/components/ResourceState.svelte";

const children = createRawSnippet(() => ({
  render: () => `<div data-the-data="1">the data</div>`
}));

const failure = { code: "network", message: "network error" };

function body(props: Record<string, unknown>): string {
  return render(ResourceState, {
    props: { loadingText: "Loading...", shape: "rows", children, ...props }
  }).body;
}

describe("ResourceState", () => {
  it("keeps the data on screen and puts the failed read above it", () => {
    const markup = body({ error: failure, hasData: true });

    expect(markup).toContain("data-the-data");
    expect(markup).toContain("network error");
    expect(markup.indexOf("error-line")).toBeLessThan(markup.indexOf("data-the-data"));
  });

  it("shows the error alone when there is nothing to show yet", () => {
    const markup = body({ error: failure, hasData: false });

    expect(markup).toContain("network error");
    expect(markup).not.toContain("data-the-data");
  });

  it("keeps stale data through a refresh without a skeleton or loading announcement", () => {
    const markup = body({ loading: true, hasData: true });
    expect(markup).toContain("data-the-data");
    expect(markup).not.toContain('aria-hidden="true"');
    expect(markup).toMatch(/role="status">(?:<!--.*?-->)*<\/span>/);
    expect(markup).not.toContain("Loading...");
  });

  it("begins a cold wait busy and silent, before admitting decorative shapes", () => {
    const markup = body({ loading: true });
    // The status closes before the busy root opens, at the same top level.
    // This protects the announcement from aria-busy deferral.
    const elements = markup.replace(/<!--[\s\S]*?-->/g, "").trim();
    expect(elements).toMatch(/^<span class="skeleton-status" role="status"><\/span>\s*<div[^>]*aria-busy="true"/);
    expect(markup).toContain('class="skeleton-status"');
    expect(markup).not.toContain('aria-hidden="true"');
    expect(markup).not.toContain("Loading...");
    expect(markup).toContain('aria-busy="true"');
    expect(markup).not.toContain("data-the-data");
  });

});
