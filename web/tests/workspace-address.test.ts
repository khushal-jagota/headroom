import { describe, expect, it } from "vitest";
import { parseWorkspaceAddress, whatTheAddressOpens, workspaceAddress } from "../src/lib/workspaceAddress";

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
