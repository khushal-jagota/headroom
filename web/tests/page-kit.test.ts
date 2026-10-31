import { describe, expect, it } from "vitest";
import exampleDocument from "../../src/planner/page_kit/examples/proposal-page.json";
import { readStoredPage } from "../src/lib/pageKit";

describe("proposal page kit", () => {
  it("reads the worked example as a page and leaves prose alone", () => {
    const page = readStoredPage(JSON.stringify(exampleDocument));
    expect(page.intent).toBe("page");
    if (page.intent !== "page") return;
    expect(page.document.title).toBe("Ship proposal pages");
    const glance = page.document.blocks.find((block) => block.type === "section" && block.title === "At a glance");
    expect(glance?.type === "section" && glance.blocks?.[0]?.type === "properties" && glance.blocks[0].items.map((item) => item.label)).toEqual([
      "Stage",
      "Owner",
      "Open choice",
      "Blockers"
    ]);
    expect(readStoredPage("  {not a page}\n").intent).toBe("prose");
    expect(readStoredPage(JSON.stringify({ kind: "note" })).intent).toBe("prose");
    const broken = readStoredPage('{ "kind": "headroom.page", "title": ');
    expect(broken.intent).toBe("invalid");
    if (broken.intent === "invalid") expect(broken.message).toContain("not valid JSON");
  });
});