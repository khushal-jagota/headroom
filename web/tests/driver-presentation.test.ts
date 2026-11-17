/** How a backend key is named and drawn from the server's driver list.
 *
 * The risk is an unknown backend: before the driver list it fell through to the Hermes
 * picture. Now it must get its own capitalised name and its initials, never a blank or
 * another driver's mark, and a list still loading must hold the place without a guess.
 */
import { describe, expect, it } from "vitest";

import {
  backendInitials,
  backendLabel,
  backendMark,
  driverFor
} from "../src/lib/drivers/presentation";
import { DRIVERS } from "./support/drivers";

describe("a backend key and the driver list", () => {
  it("draws a known driver with its own label and icon", () => {
    expect(driverFor("codex", DRIVERS)?.label).toBe("Codex");
    expect(backendLabel("claude", DRIVERS)).toBe("Claude");
    expect(backendMark("claude", DRIVERS)).toEqual({
      kind: "icon", url: "/api/drivers/claude/icon?v=cccccccccccc", picture: false
    });
  });

  it("draws a full-colour icon as a picture, the way Hermes always drew", () => {
    expect(backendMark("hermes", DRIVERS)).toEqual({
      kind: "icon", url: "/api/drivers/hermes/icon?v=aaaaaaaaaaaa", picture: true
    });
  });

  it("gives an unknown key its capitalised name and its initials, not another mark", () => {
    expect(driverFor("antigravity", DRIVERS)).toBeNull();
    expect(backendLabel("antigravity", DRIVERS)).toBe("Antigravity");
    expect(backendMark("antigravity", DRIVERS)).toEqual({ kind: "initials", text: "A" });
  });

  it("takes the first letter of up to two parts of a hyphenated or underscored key", () => {
    expect(backendInitials("open-code")).toBe("OC");
    expect(backendInitials("scripted_agent_three")).toBe("SA");
    expect(backendMark("open-code", DRIVERS)).toEqual({ kind: "initials", text: "OC" });
  });

  it("holds the place while the list is pending, and still names the key", () => {
    expect(backendMark("codex", undefined)).toEqual({ kind: "pending" });
    expect(backendMark("open-code", undefined)).toEqual({ kind: "pending" });
    expect(backendLabel("codex", undefined)).toBe("Codex");
    expect(driverFor("codex", undefined)).toBeNull();
  });
});
