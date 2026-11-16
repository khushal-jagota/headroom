import { expect, it } from "vitest";
import { previewHashHref, resolvePreview, targetFromHref, targetFromPreviewHref } from "../src/lib/filePreview";

it("keeps an HTML link's query and fragment through the preview route", () => {
  const target = targetFromHref('/files/tickets/t_test/site/index.html?mode=demo#part');
  if (target.kind !== 'ticket-file') throw new Error('expected managed file');
  const restored = targetFromPreviewHref(previewHashHref(target));
  expect(restored && resolvePreview(restored).href).toBe('/files/tickets/t_test/site/index.html?mode=demo#part');
});
