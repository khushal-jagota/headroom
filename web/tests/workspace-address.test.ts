import { describe, expect, it } from "vitest";
import { correctedWorkspaceAddress, parseWorkspaceAddress, whatTheAddressOpens, workspaceAddress } from "../src/lib/workspaceAddress";

describe("workspace utility addresses", () => {
  it.each(["usage", "settings", "ideas", "feedback"] as const)(
    "round trips %s as a selected workspace utility",
    (utility) => {
      const href = workspaceAddress({ kind: "utility", utility, path: [] });
      const address = parseWorkspaceAddress(href);
      expect(address?.selection).toEqual({ kind: "utility", utility, path: [] });
      expect(whatTheAddressOpens(address!).markedUtility).toBe(utility);
      expect(whatTheAddressOpens(address!).markedUtilityPath).toEqual([]);
      expect(workspaceAddress(address!.selection, address!.view, address!.openFile)).toBe(href);
    }
  );

  it("keeps unsupported utility names out of the workspace selection", () => {
    expect(parseWorkspaceAddress("#/workspace/utility/unknown")).toBeNull();
  });
});

describe("a position inside a utility the pane is showing", () => {
  it.each([
    ["#/workspace/utility/usage/tokens", "usage", ["tokens"]],
    ["#/workspace/utility/usage/config/workers/coding", "usage", ["config", "workers", "coding"]],
    ["#/workspace/utility/settings/scheduled-tasks", "settings", ["scheduled-tasks"]],
    ["#/workspace/utility/settings/config/chief-of-staff", "settings", ["config", "chief-of-staff"]]
  ] as const)("round trips %s, so a reload or a paste lands where the click did", (href, utility, path) => {
    const address = parseWorkspaceAddress(href);
    expect(address?.selection).toEqual({ kind: "utility", utility, path: [...path] });
    expect(whatTheAddressOpens(address!).markedUtilityPath).toEqual([...path]);
    expect(workspaceAddress(address!.selection, address!.view, address!.openFile)).toBe(href);
  });

  it("keeps the rail on the list the reader was using", () => {
    const href = workspaceAddress(
      { kind: "utility", utility: "usage", path: ["tokens"] },
      "managers"
    );
    expect(href).toBe("#/workspace/utility/usage/tokens?view=managers");
    const address = parseWorkspaceAddress(href);
    expect(address?.view).toBe("managers");
    expect(whatTheAddressOpens(address!).markedUtilityPath).toEqual(["tokens"]);
  });

  it.each([
    "#/workspace/utility/usage/unknown",
    "#/workspace/utility/usage/tokens/detail",
    "#/workspace/utility/settings/unknown",
    "#/workspace/utility/settings/notifications/detail",
    "#/workspace/utility/ideas/anything",
    "#/workspace/utility/feedback/anything"
  ])("refuses an address no screen has a position for: %s", (href) => {
    expect(parseWorkspaceAddress(href)).toBeNull();
  });
});

describe("a bare id that turns out to name a manager", () => {
  const managers = (...ids: string[]) => (id: string) => ids.includes(id);

  it("corrects the address rather than opening a Ticket page that says not found", () => {
    const address = parseWorkspaceAddress("#/workspace/item_abc");
    expect(address).not.toBeNull();
    expect(correctedWorkspaceAddress(address!, managers("item_abc"))).toBe(
      "#/workspace/manager/item_abc"
    );
  });

  it("leaves every address alone that is not a misread manager", () => {
    const cases = [
      // An ordinary Ticket. The publication knows it is not a manager.
      "#/workspace/t_abc",
      // Already a manager address.
      "#/workspace/manager/item_abc",
      // Unambiguous: the trailing id is a Ticket inside a named Item.
      "#/workspace/manager/item_abc/t_abc",
      "#/workspace",
      "#/workspace/chief-of-staff",
      "#/workspace/utility/usage"
    ];
    for (const hash of cases) {
      const address = parseWorkspaceAddress(hash);
      expect(address, hash).not.toBeNull();
      expect(correctedWorkspaceAddress(address!, managers("item_abc")), hash).toBeNull();
    }
  });

  it("does not correct anything while the publication is still empty", () => {
    // Before the publication arrives nothing is known to be a manager, and a
    // correction made on no evidence would send a real Ticket to the wrong page.
    const address = parseWorkspaceAddress("#/workspace/item_abc");
    expect(correctedWorkspaceAddress(address!, managers())).toBeNull();
  });
});

// Retained addresses are decoded at the boundary. Every new address uses Manager names.
it("decodes old Item links and emits canonical Manager paths and view queries", () => {
  const old = parseWorkspaceAddress("#/workspace/item/si_existing/t_existing?view=items");
  expect(old?.selection).toEqual({kind:"ticket",id:"t_existing",openedFromManagerId:"si_existing"});
  expect(old?.view).toBe("managers");
  expect(workspaceAddress(old!.selection, old!.view)).toBe("#/workspace/manager/si_existing/t_existing");
  expect(workspaceAddress({kind:"none"}, old!.view)).toBe("#/workspace?view=managers");
});

it("keeps the exact conversation when review opens a historical message", () => {
  const href = workspaceAddress({kind:"ticket",id:"t_history"}, undefined, null, "old/conversation");
  const address = parseWorkspaceAddress(href)!;
  expect(address.selection).toEqual({kind:"ticket",id:"t_history"});
  expect(address.selectedConversationId).toBe("old/conversation");
  expect(workspaceAddress(address.selection,address.view,address.openFile,address.selectedConversationId)).toBe(href);
});
