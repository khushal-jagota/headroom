import { render } from "svelte/server";
import AppWithQueryClient from "../src/AppWithQueryClient.svelte";
import { describe, expect, it, vi } from "vitest";
import { feedbackPageContext, feedbackPageHref } from "../src/lib/feedback";
import { canonicalShellHash } from "../src/lib/shellRouting";
import { nestedHref, parseUtilityPath, prefixHref } from "../src/lib/utilityAddress";

describe("historical shell addresses", () => {
  it.each([
    ["#/backlog", "#/workspace"],
    ["#/backlog?project=old", "#/workspace"],
    ["#/notifications", "#/settings/notifications"],
    ["#/scheduled-tasks", "#/settings/scheduled-tasks"],
    ["#/config", "#/settings/config"],
    ["#/config/workers/type%2Fwith%20space", "#/settings/config/workers/type%2Fwith%20space"],
    ["#/config/chief-of-staff", "#/settings/config/chief-of-staff"],
    ["#/config/worker-skill", "#/settings/config/worker-skill"],
    ["#/config/sprint-item-supervisor", "#/settings/config/sprint-item-supervisor"],
    ["#/workers", "#/settings/config"],
    ["#/workers?legacy=true", "#/settings/config?legacy=true"],
    ["#/notifications?legacy=true", "#/settings/notifications?legacy=true"],
    ["#/workers/coding", "#/settings/config/workers/coding"],
    ["#/agents/workers/coding", "#/settings/config/workers/coding"],
    ["#/agents/worker-skill", "#/settings/config/worker-skill"],
    ["#/agents/chief-of-staff", "#/workspace/chief-of-staff"],
    ["#/agents", "#/workspace"]
  ])("redirects %s without losing its destination", (old, current) => {
    expect(canonicalShellHash(old)).toBe(current);
    expect(canonicalShellHash(current)).toBe(current);
  });
  it("leaves unknown routes explicit", () => {
    expect(canonicalShellHash("#/config/unknown/detail")).toBe("#/settings/config/unknown/detail");
    expect(canonicalShellHash("#/agents/unknown")).toBe("#/agents/unknown");
  });
});

it("records Settings feedback against its actual section address", () => {
  for (const [hash, label] of [["#/settings", "Settings · Config"], ["#/settings/config/workers/coding", "Settings · Config"], ["#/settings/scheduled-tasks", "Settings · Scheduled tasks"], ["#/settings/notifications", "Settings · Notifications"]]) {
    expect(feedbackPageContext(hash)).toEqual({address: hash, label});
  }
});

it("captures new feedback on Chief and retains saved historical links", () => {
  expect(feedbackPageHref("#/day/2026-07-29")).toBe("#/day/2026-07-29");
  expect(feedbackPageHref("#/sprint?sprint=old")).toBe("#/sprint?sprint=old");
  for (const hash of ["", "#", "#/workspace/chief-of-staff"]) {
    expect(feedbackPageContext(hash)).toEqual({ address: "#/workspace/chief-of-staff", label: "Chief of Staff" });
  }
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

// The Settings rules used to live inline in the shell router, where the pane could not
// reach them. They moved so both roots read one copy, and they had no test of their own.
describe("where a utility screen can be", () => {
  it("defaults Settings to Config and accepts its two flat sections", () => {
    expect(parseUtilityPath("settings", [])).toEqual({
      utility: "settings", settings: { section: "config", roleKind: "index" }
    });
    expect(parseUtilityPath("settings", ["scheduled-tasks"])).toEqual({
      utility: "settings", settings: { section: "scheduled-tasks", roleKind: "index" }
    });
    expect(parseUtilityPath("settings", ["config", "workers", "coding"])).toEqual({
      utility: "settings", settings: { section: "config", roleKind: "worker", roleId: "coding" }
    });
  });

  it.each([
    ["settings", ["unknown"]],
    ["settings", ["notifications", "detail"]],
    ["settings", ["config", "unknown"]],
    ["usage", ["unknown"]],
    ["ideas", ["anything"]],
    ["feedback", ["anything"]]
  ] as const)("refuses %s/%j", (utility, path) => {
    expect(parseUtilityPath(utility, path as unknown as string[])).toBeNull();
  });

  it("builds a page address by appending, and nothing at all for the default", () => {
    const href = prefixHref("#/settings");
    expect(href([])).toBe("#/settings");
    expect(href(["config", "workers", "coding"])).toBe("#/settings/config/workers/coding");
    expect(nestedHref(href, ["config"])(["chief-of-staff"]))
      .toBe("#/settings/config/chief-of-staff");
  });
});
