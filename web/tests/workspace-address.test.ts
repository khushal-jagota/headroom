import { describe, expect, it } from "vitest";
import { correctedWorkspaceAddress, parseWorkspaceAddress, whatTheAddressOpens, workspaceAddress } from "../src/lib/workspaceAddress";

describe("workspace utility addresses", () => {
  it.each(["usage", "settings", "ideas", "feedback"] as const)(
    "round trips %s as a selected workspace utility",
    (utility) => {
      const href = workspaceAddress({ kind: "utility", utility });
      const address = parseWorkspaceAddress(href);
      expect(address?.selection).toEqual({ kind: "utility", utility });
      expect(whatTheAddressOpens(address!).markedUtility).toBe(utility);
      expect(workspaceAddress(address!.selection, address!.view, address!.openFile)).toBe(href);
    }
  );

  it("keeps unsupported utility names out of the workspace selection", () => {
    expect(parseWorkspaceAddress("#/workspace/utility/unknown")).toBeNull();
  });
});

describe("a bare id that turns out to name an orchestrator", () => {
  const orchestrators = (...ids: string[]) => (id: string) => ids.includes(id);

  it("corrects the address rather than opening a Ticket page that says not found", () => {
    const address = parseWorkspaceAddress("#/workspace/item_abc");
    expect(address).not.toBeNull();
    expect(correctedWorkspaceAddress(address!, orchestrators("item_abc"))).toBe(
      "#/workspace/item/item_abc"
    );
  });

  it("leaves every address alone that is not a misread orchestrator", () => {
    const cases = [
      // An ordinary Ticket. The publication knows it is not an orchestrator.
      "#/workspace/t_abc",
      // Already an orchestrator address.
      "#/workspace/item/item_abc",
      // Unambiguous: the trailing id is a Ticket inside a named Item.
      "#/workspace/item/item_abc/t_abc",
      "#/workspace",
      "#/workspace/chief-of-staff",
      "#/workspace/utility/usage"
    ];
    for (const hash of cases) {
      const address = parseWorkspaceAddress(hash);
      expect(address, hash).not.toBeNull();
      expect(correctedWorkspaceAddress(address!, orchestrators("item_abc")), hash).toBeNull();
    }
  });

  it("does not correct anything while the publication is still empty", () => {
    // Before the publication arrives nothing is known to be an orchestrator, and a
    // correction made on no evidence would send a real Ticket to the wrong page.
    const address = parseWorkspaceAddress("#/workspace/item_abc");
    expect(correctedWorkspaceAddress(address!, orchestrators())).toBeNull();
  });
});
