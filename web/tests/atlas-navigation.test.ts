import { describe, expect, it } from "vitest";

import { selectionForHref, selectionForLinkClick } from "../src/lib/atlas/navigation";

const plainClick = { button: 0 };

describe("an href inside the Atlas panel", () => {
  it("finds each supported Atlas selection", () => {
    const cases = [
      ["#/workspace/t_abc123", { kind: "ticket", id: "t_abc123" }],
      ["#/workspace/item/si_xyz/t_abc123", { kind: "ticket", id: "t_abc123" }],
      ["#/workspace/item/si_xyz", { kind: "item", id: "si_xyz" }],
      ["/#/workspace/t_abc123", { kind: "ticket", id: "t_abc123" }],
      ["#/sprint?item=si_xyz", { kind: "item", id: "si_xyz" }],
      ["#/sprint?item=si%20x", { kind: "item", id: "si x" }],
      ["#/workspace/t%20a", { kind: "ticket", id: "t a" }]
    ] as const;

    for (const [href, selection] of cases) {
      expect(selectionForHref(href)).toEqual(selection);
    }
  });

  it("leaves alone what is no place in the world", () => {
    expect(selectionForHref("#/workspace")).toBeNull();
    expect(selectionForHref("#/workspace/chief-of-staff")).toBeNull();
    expect(selectionForHref("#/preview?source=ticket&ticket=t_abc123")).toBeNull();
    expect(selectionForHref("#/sprint")).toBeNull();
    expect(selectionForHref("#/day")).toBeNull();
    expect(selectionForHref("https://example.com/thing")).toBeNull();
    expect(selectionForHref("/files/tickets/t_abc123/artifacts/plan.html")).toBeNull();
    expect(selectionForHref("")).toBeNull();
    expect(selectionForHref(null)).toBeNull();
  });
});

describe("the click on that link", () => {
  it("moves Atlas on a plain click", () => {
    expect(selectionForLinkClick({ href: "#/workspace/t_abc123" }, plainClick)).toEqual({
      kind: "ticket",
      id: "t_abc123"
    });
  });

  it("is left to the reader when it asks for another tab or window", () => {
    const link = { href: "#/workspace/t_abc123" };
    expect(selectionForLinkClick(link, { button: 1 })).toBeNull();
    expect(selectionForLinkClick(link, { button: 0, metaKey: true })).toBeNull();
    expect(selectionForLinkClick(link, { button: 0, ctrlKey: true })).toBeNull();
    expect(selectionForLinkClick(link, { button: 0, shiftKey: true })).toBeNull();
    expect(selectionForLinkClick(link, { button: 0, altKey: true })).toBeNull();
    expect(
      selectionForLinkClick({ href: "#/workspace/t_abc123", target: "_blank" }, plainClick)
    ).toBeNull();
  });
});
