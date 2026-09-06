import { describe, expect, it } from "vitest";

import {
  parseWorkspaceAddress,
  workspaceAddress,
  type WorkspaceSelection
} from "../src/lib/workspaceAddress";

describe("Workspace addresses", () => {
  it("parses the rail, Chief, Ticket, and Sprint Item identities", () => {
    expect(parseWorkspaceAddress("#/workspace")).toEqual({
      selection: { kind: "none" },
      view: "tickets",
      openFile: null
    });
    expect(parseWorkspaceAddress("#/workspace/chief-of-staff")).toEqual({
      selection: { kind: "chief" },
      view: "items",
      openFile: null
    });
    // A Ticket that names no Item is the one thing the Sprint Items list cannot draw.
    expect(parseWorkspaceAddress("#/workspace/ticket%20one")).toEqual({
      selection: { kind: "ticket", id: "ticket one" },
      view: "tickets",
      openFile: null
    });
    expect(parseWorkspaceAddress("#/workspace/item/item%2Fone")).toEqual({
      selection: { kind: "item", id: "item/one" },
      view: "items",
      openFile: null
    });
  });

  it("writes encoded, shareable addresses", () => {
    const selection = { kind: "item", id: "item / one" } as const;
    expect(workspaceAddress(selection)).toBe("#/workspace/item/item%20%2F%20one");
    expect(parseWorkspaceAddress(workspaceAddress(selection))?.selection).toEqual(
      selection
    );
  });

  it("carries the artifact a Ticket has open", () => {
    const file = { kind: "ticket-file", ticketId: "t_one", path: "artifacts/plan.html" } as const;
    const address = workspaceAddress({ kind: "ticket", id: "t_one" }, undefined, file);
    expect(address).toBe(
      "#/workspace/t_one?source=ticket&ticket=t_one&path=artifacts%2Fplan.html"
    );
    expect(parseWorkspaceAddress(address)).toEqual({
      selection: { kind: "ticket", id: "t_one" },
      view: "tickets",
      openFile: file
    });
  });

  // Only a Ticket draws an artifact, and an unsafe path names no file at all.
  it("rejects malformed Workspace paths", () => {
    expect(parseWorkspaceAddress("#/workspace/item")).toBeNull();
    expect(parseWorkspaceAddress("#/workspace/item/one/two/three")).toBeNull();
    expect(parseWorkspaceAddress("#/ticket/one")).toBeNull();
  });
});
