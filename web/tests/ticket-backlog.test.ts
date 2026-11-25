import { render } from "svelte/server";
import { describe, expect, it, vi } from "vitest";
import TicketLifecycleControls from "../src/components/TicketLifecycleControls.svelte";
import type { TicketDetail } from "../src/lib/types";
import { recheckWorkerCleanup, workerCleanupMessage } from "../src/lib/workerCleanup";

const ticket = (overrides: Partial<TicketDetail> = {}): TicketDetail => ({
  id: "ticket-example", stage: "needs_implementation", is_backlogged: false,
  record_sha256: "fresh-record", field_values: {}, ...overrides
} as TicketDetail);

function controls(value: TicketDetail, terminal = false): string {
  return render(TicketLifecycleControls, { props: { ticket: value, terminal } }).body;
}

describe("explicit ticket lifecycle controls", () => {
  it("offers deferral independently of unscheduled placement", () => {
    const markup = controls(ticket({ manager_id: null }));
    expect(markup).toContain("Backlog ticket");
    expect(markup).not.toContain("Ticket actions");
    expect(markup).not.toContain("<details");
    expect(markup).not.toContain("Retire ticket");
    expect(markup).not.toContain("data-retirement-input");
    expect(markup).not.toContain("Resume ticket");
  });

  it("offers resume and preserves the contact and approval explanation", () => {
    const markup = controls(ticket({ is_backlogged: true, }));
    expect(markup).toContain("Resume ticket");
    expect(markup).not.toContain("Retire ticket");
    expect(markup).not.toContain("Ticket actions");
    expect(markup).toContain("Automatic work is paused. Messages and approvals remain available.");
  });

  it("keeps resume available alongside owner and manager proposal facts", () => {
    for (const review of [{ awaiting_approval:true }, { awaiting_agent_approval:true }]) {
      const value = ticket({ is_backlogged:true, ...review,
        pending_proposal:{ field:"implementation", body:"Retained proposal", proposed_by:"worker", created_at:1, revision:3 } });
      expect(controls(value)).toContain("Resume ticket");
    }
    expect(controls(ticket({ stage:"done", is_backlogged:true }), true)).not.toContain("data-ticket-backlog-action");
    expect(controls(ticket({ stage:"done" }), true)).toContain("Recheck Worker cleanup");
  });
});

it("rechecks exact cleanup and preserves an unproven result without a start request", async () => {
  const requests: Array<{ url: string; method: string }> = [];
  vi.stubGlobal("fetch", async (input: string, options: RequestInit) => {
    requests.push({ url: input, method: String(options.method) });
    return new Response(JSON.stringify({ complete: false, reason: "Recorded tool remains" }), { status: 200 });
  });
  try {
    const result = await recheckWorkerCleanup("ended ticket");
    expect(requests).toEqual([{ url: "/api/tickets/ended%20ticket/recheck-worker-cleanup", method: "POST" }]);
    expect(workerCleanupMessage(result)).toBe("Worker cleanup not proven. Recorded tool remains");
  } finally { vi.unstubAllGlobals(); }
});
