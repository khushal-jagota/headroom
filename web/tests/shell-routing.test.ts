import { describe, expect, it } from "vitest";
import { feedbackPageContext } from "../src/lib/feedback";
import { canonicalShellHash } from "../src/lib/shellRouting";

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
