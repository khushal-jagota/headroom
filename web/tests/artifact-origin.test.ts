import { describe, expect, it } from "vitest";
import { artifactPageUrl } from "../src/lib/artifactOrigin";

const panels = "https://panels.example";
describe("artifact page address", () => {
  it("preserves the file path, query and fragment on the separate origin", () => {
    expect(artifactPageUrl("https://files.example", "/files/tickets/t/a%20b.html?view=1#part", panels))
      .toBe("https://files.example/files/tickets/t/a%20b.html?view=1#part");
  });
  it("does not run a page on the Panels origin or accept a non-file address", () => {
    for (const origin of [null, panels, "javascript:alert(1)", "https://files.example/path"]) {
      expect(() => artifactPageUrl(origin, "/files/tickets/t/a.html", panels)).toThrow();
    }
    expect(() => artifactPageUrl("https://files.example", "https://other.example/files/a.html", panels)).toThrow();
    expect(() => artifactPageUrl("https://files.example", "/api/tickets", panels)).toThrow();
  });
});

import { previewHashHref, resolvePreview, targetFromHref, targetFromPreviewHref } from "../src/lib/filePreview";
it("keeps an HTML link's query and fragment through the preview route", () => {
  const target = targetFromHref('/files/tickets/t_test/site/index.html?mode=demo#part');
  if (target.kind !== 'ticket-file') throw new Error('expected managed file');
  const restored = targetFromPreviewHref(previewHashHref(target));
  expect(restored && resolvePreview(restored).href).toBe('/files/tickets/t_test/site/index.html?mode=demo#part');
});
