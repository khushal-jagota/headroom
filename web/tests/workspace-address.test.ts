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
      view: "items",
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

  it("carries the Item a Ticket was opened from", () => {
    expect(parseWorkspaceAddress("#/workspace/item/si%20one/t%20one")).toEqual({
      selection: { kind: "ticket", id: "t one", openedFromItemId: "si one" },
      view: "items",
      openFile: null
    });
    const selection: WorkspaceSelection = {
      kind: "ticket",
      id: "t / one",
      openedFromItemId: "si / one"
    };
    expect(workspaceAddress(selection)).toBe(
      "#/workspace/item/si%20%2F%20one/t%20%2F%20one"
    );
    expect(parseWorkspaceAddress(workspaceAddress(selection))?.selection).toEqual(
      selection
    );
  });

  it("writes encoded, shareable addresses", () => {
    const selection = { kind: "item", id: "item / one" } as const;
    expect(workspaceAddress(selection)).toBe("#/workspace/item/item%20%2F%20one");
    expect(parseWorkspaceAddress(workspaceAddress(selection))?.selection).toEqual(
      selection
    );
  });

  it("writes the view only when the reader asked for the other one", () => {
    // Everything but a Ticket on its own already says Sprint Items.
    expect(workspaceAddress({ kind: "item", id: "si_one" }, "items")).toBe(
      "#/workspace/item/si_one"
    );
    expect(workspaceAddress({ kind: "chief" }, "items")).toBe(
      "#/workspace/chief-of-staff"
    );
    expect(workspaceAddress({ kind: "chief" }, "tickets")).toBe(
      "#/workspace/chief-of-staff?view=tickets"
    );
    expect(workspaceAddress({ kind: "ticket", id: "t_one" }, "tickets")).toBe(
      "#/workspace/t_one"
    );
    expect(workspaceAddress({ kind: "item", id: "si_one" }, "tickets")).toBe(
      "#/workspace/item/si_one?view=tickets"
    );
  });

  it("reads the view back, and ignores a view it does not know", () => {
    expect(parseWorkspaceAddress("#/workspace?view=items")).toEqual({
      selection: { kind: "none" },
      view: "items",
      openFile: null
    });
    expect(parseWorkspaceAddress("#/workspace/item/si_one?view=tickets")).toEqual({
      selection: { kind: "item", id: "si_one" },
      view: "tickets",
      openFile: null
    });
    expect(parseWorkspaceAddress("#/workspace?view=sideways")).toEqual({
      selection: { kind: "none" },
      view: "items",
      openFile: null
    });
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

  it("keeps the view and the artifact in one address", () => {
    const file = { kind: "sprint-item-file", sprintItemId: "si_one", path: "notes.md" } as const;
    const address = workspaceAddress(
      { kind: "ticket", id: "t_one", openedFromItemId: "si_one" },
      "tickets",
      file
    );
    expect(address).toBe(
      "#/workspace/item/si_one/t_one?view=tickets&source=sprint-item&item=si_one&path=notes.md"
    );
    expect(parseWorkspaceAddress(address)?.openFile).toEqual(file);
  });

  // Only a Ticket draws an artifact, and an unsafe path names no file at all.
  it("drops a file no screen here could show", () => {
    expect(
      parseWorkspaceAddress("#/workspace?source=ticket&ticket=t_one&path=plan.md")?.openFile
    ).toBeNull();
    expect(
      parseWorkspaceAddress("#/workspace/item/si_one?source=sprint-item&item=si_one&path=a.md")
        ?.openFile
    ).toBeNull();
    expect(
      parseWorkspaceAddress("#/workspace/t_one?source=ticket&ticket=t_one&path=../secret.md")
        ?.openFile
    ).toBeNull();
    expect(
      workspaceAddress(
        { kind: "item", id: "si_one" },
        undefined,
        { kind: "ticket-file", ticketId: "t_one", path: "plan.md" }
      )
    ).toBe("#/workspace/item/si_one");
  });

  it("rejects malformed Workspace paths", () => {
    expect(parseWorkspaceAddress("#/workspace/item")).toBeNull();
    expect(parseWorkspaceAddress("#/workspace/item/one/two/three")).toBeNull();
    expect(parseWorkspaceAddress("#/ticket/one")).toBeNull();
  });
});
