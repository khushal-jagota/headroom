import { describe, expect, it } from "vitest";

import { selectionForHref, selectionForLinkClick } from "../src/lib/atlas/navigation";

const plainClick = { button: 0 };

describe("an href inside the Atlas panel", () => {
  it("finds the Ticket a Workspace address names", () => {
    expect(selectionForHref("#/workspace/t_abc123")).toEqual({
      kind: "ticket",
      id: "t_abc123"
    });
  });

  it("finds the Ticket opened from inside an Item", () => {
    expect(selectionForHref("#/workspace/item/si_xyz/t_abc123")).toEqual({
      kind: "ticket",
      id: "t_abc123"
    });
  });

  it("finds the Item", () => {
    expect(selectionForHref("#/workspace/item/si_xyz")).toEqual({
      kind: "item",
      id: "si_xyz"
    });
  });

  it("reads the form the server builds with a leading slash", () => {
    expect(selectionForHref("/#/workspace/t_abc123")).toEqual({
      kind: "ticket",
      id: "t_abc123"
    });
  });

  it("reads the supervisor panel's own link to its Item", () => {
    expect(selectionForHref("#/sprint?item=si_xyz")).toEqual({
      kind: "item",
      id: "si_xyz"
    });
  });

  it("decodes an id the address escaped", () => {
    expect(selectionForHref("#/sprint?item=si%20x")).toEqual({ kind: "item", id: "si x" });
    expect(selectionForHref("#/workspace/t%20a")).toEqual({ kind: "ticket", id: "t a" });
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
