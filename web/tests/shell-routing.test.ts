import { render } from "svelte/server";
import AppWithQueryClient from "../src/AppWithQueryClient.svelte";
import { describe, expect, it, vi } from "vitest";
import { canonicalShellHash } from "../src/lib/shellRouting";
import { parseSettingsPath, settingsHref } from "../src/lib/settings/address";

describe("historical shell addresses", () => {
  // Every Settings address from before the redesign lands on its new section or detail page.
  it.each([
    ["#/backlog", "#/workspace"],
    ["#/backlog?project=old", "#/workspace"],
    ["#/workspace/item/si_existing?view=items", "#/workspace/manager/si_existing?view=managers"],
    ["#/settings/config", "#/settings/general"],
    ["#/settings/config/chief-of-staff", "#/settings/agents/chief-of-staff"],
    ["#/settings/config/manager", "#/settings/agents/manager"],
    ["#/settings/config/worker-skill", "#/settings/agents/worker-skill"],
    ["#/settings/config/workers/type%2Fwith%20space", "#/settings/workers/type%2Fwith%20space"],
    ["#/settings/vps", "#/settings/diagnostics"],
    ["#/vps", "#/settings/diagnostics"],
    ["#/notifications", "#/settings/notifications"],
    ["#/notifications?legacy=true", "#/settings/notifications?legacy=true"],
    ["#/config", "#/settings/general"],
    ["#/config/chief-of-staff", "#/settings/agents/chief-of-staff"],
    ["#/config/manager", "#/settings/agents/manager"],
    ["#/config/worker-skill", "#/settings/agents/worker-skill"],
    ["#/config/workers/coding", "#/settings/workers/coding"],
    ["#/workers", "#/settings/workers"],
    ["#/workers?legacy=true", "#/settings/workers?legacy=true"],
    ["#/workers/coding", "#/settings/workers/coding"],
    ["#/agents/workers/coding", "#/settings/workers/coding"],
    ["#/agents/worker-skill", "#/settings/agents/worker-skill"],
    ["#/backends/config", "#/settings/agents"],
    ["#/backends/config/manager", "#/settings/agents/manager"],
    ["#/backends/config/workers/coding", "#/settings/workers/coding"],
    ["#/workspace/utility/settings", "#/settings"],
    ["#/workspace/utility/settings/notifications?view=managers", "#/settings/notifications?view=managers"],
    ["#/workspace/utility/settings/config/workers/coding", "#/settings/workers/coding"],
    ["#/workspace/utility/settings/vps", "#/settings/diagnostics"],
    ["#/workspace/utility/usage/config/chief-of-staff", "#/settings/agents/chief-of-staff"],
    ["#/agents/chief-of-staff", "#/workspace/chief-of-staff"],
    ["#/agents", "#/workspace"]
  ])("redirects %s without losing its destination", (old, current) => {
    expect(canonicalShellHash(old)).toBe(current);
    expect(canonicalShellHash(current)).toBe(current);
  });
  it("leaves unknown routes explicit", () => {
    expect(canonicalShellHash("#/config/unknown/detail")).toBe("#/settings/config/unknown/detail");
    expect(canonicalShellHash("#/agents/unknown")).toBe("#/agents/unknown");
    expect(canonicalShellHash("#/settings/config/sprint-item-supervisor"))
      .toBe("#/settings/config/sprint-item-supervisor");
  });
});

it("uses the unavailable screen for archived calendar routes", () => {
  for (const hash of ["#/day/2026-07-29", "#/sprint", "#/sprint/documents?sprint=old"]) {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { hash, replace } });
    try {
      expect(render(AppWithQueryClient).body).toContain("no such screen");
      expect(replace).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  }
});

it("preserves the bare Home redirect to Chief", () => {
  for (const hash of ["", "#", "#/day"]) {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { hash, replace } });
    try {
      expect(render(AppWithQueryClient).body).not.toContain("no such screen");
      expect(replace).toHaveBeenCalledWith("#/workspace/chief-of-staff");
    } finally {
      vi.unstubAllGlobals();
    }
  }
});

describe("where a Settings page can be", () => {
  it("opens General on the bare address without choosing it", () => {
    expect(parseSettingsPath([])).toEqual({ section: "general", detail: null, chosen: false });
    expect(parseSettingsPath(["notifications"])).toEqual({ section: "notifications", detail: null, chosen: true });
    expect(parseSettingsPath(["agents", "manager"])).toEqual({ section: "agents", detail: "manager", chosen: true });
    expect(parseSettingsPath(["workers", "type%2Fwith%20space"])).toEqual({ section: "workers", detail: "type/with space", chosen: true });
    expect(settingsHref("workers", "type/with space")).toBe("#/settings/workers/type%2Fwith%20space");
  });

  it.each([
    [["unknown"]], [["config"]], [["notifications", "detail"]], [["agents", "unknown"]],
    [["workers", "coding", "extra"]], [["vps"]]
  ])("refuses %j", path => {
    expect(parseSettingsPath(path)).toBeNull();
  });
});
