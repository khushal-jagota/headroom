/** How a backend key is named and drawn from the server's driver list.
 *
 * The risk is an unknown backend, such as a saved Hermes key now that Hermes is not built in.
 * It must get its own capitalised name and its initials, never a blank or another driver's
 * mark, and a list still loading must hold the place without a guess.
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

  it("draws a full-colour icon as a picture", () => {
    const picture = {
      driver_kind: "picture", label: "Picture", accent_color: "#a78bfa", checks_for_updates: false,
      icon: { url: "/api/drivers/picture/icon?v=aaaaaaaaaaaa", media_type: "image/png", monochrome: false }
    } as const;
    expect(backendMark("picture", [...DRIVERS, picture])).toEqual({
      kind: "icon", url: "/api/drivers/picture/icon?v=aaaaaaaaaaaa", picture: true
    });
  });

  it("gives an unknown key its capitalised name and its initials, not another mark", () => {
    expect(driverFor("hermes", DRIVERS)).toBeNull();
    expect(backendLabel("hermes", DRIVERS)).toBe("Hermes");
    expect(backendMark("hermes", DRIVERS)).toEqual({ kind: "initials", text: "H" });
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
