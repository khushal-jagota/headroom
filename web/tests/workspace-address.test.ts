import { describe, expect, it } from "vitest";

import {
  parseWorkspaceAddress,
  workspaceAddress,
  workspaceSelectionKey
} from "../src/lib/workspaceAddress";

describe("Workspace addresses", () => {
  it("parses the rail, Chief, Ticket, and Sprint Item identities", () => {
    expect(parseWorkspaceAddress("#/workspace")).toEqual({ kind: "none" });
    expect(parseWorkspaceAddress("#/workspace/chief-of-staff")).toEqual({ kind: "chief" });
    expect(parseWorkspaceAddress("#/workspace/ticket%20one")).toEqual({
      kind: "ticket",
      id: "ticket one"
    });
    expect(parseWorkspaceAddress("#/workspace/item/item%2Fone")).toEqual({
      kind: "item",
      id: "item/one"
    });
  });

  it("writes encoded, shareable addresses with stable history keys", () => {
    const selection = { kind: "item", id: "item / one" } as const;
    expect(workspaceAddress(selection)).toBe("#/workspace/item/item%20%2F%20one");
    expect(parseWorkspaceAddress(workspaceAddress(selection))).toEqual(selection);
    expect(workspaceSelectionKey(selection)).toBe("workspace/item/item / one");
    expect(workspaceSelectionKey({ kind: "ticket", id: "item / one" })).not.toBe(
      workspaceSelectionKey(selection)
    );
  });

  it("rejects malformed Workspace paths", () => {
    expect(parseWorkspaceAddress("#/workspace/item")).toBeNull();
    expect(parseWorkspaceAddress("#/workspace/item/one/extra")).toBeNull();
    expect(parseWorkspaceAddress("#/ticket/one")).toBeNull();
  });
});
