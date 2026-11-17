import type { DriverPresentation } from "../../src/lib/drivers/contracts";

/** The server's driver list as GET /api/drivers sends it, in its order. */
export const DRIVERS: readonly DriverPresentation[] = [
  {
    driver_kind: "codex", label: "Codex", accent_color: "#e6e6e6", checks_for_updates: true,
    icon: { url: "/api/drivers/codex/icon?v=bbbbbbbbbbbb", media_type: "image/svg+xml", monochrome: true }
  },
  {
    driver_kind: "claude", label: "Claude", accent_color: "#d97757", checks_for_updates: true,
    icon: { url: "/api/drivers/claude/icon?v=cccccccccccc", media_type: "image/svg+xml", monochrome: true }
  }
];
