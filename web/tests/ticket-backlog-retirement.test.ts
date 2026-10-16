import { render } from "svelte/server";
import { describe, expect, it, vi } from "vitest";
import TicketLifecycleControls from "../src/components/TicketLifecycleControls.svelte";
import { queries } from "../src/lib/queryCatalogue";
import { retirementReason } from "../src/lib/ticketRetirement";
import type { TicketDetail } from "../src/lib/types";

const ticket = (overrides: Partial<TicketDetail> = {}): TicketDetail => ({
  id: "ticket-example", stage: "needs_implementation", is_backlogged: false,
  record_sha256: "fresh-record", field_values: {}, ...overrides
} as TicketDetail);

function controls(value: TicketDetail, terminal = false): string {
  return render(TicketLifecycleControls, { props: { ticket: value, terminal } }).body;
}

describe("explicit ticket lifecycle controls", () => {
  it("offers deferral independently of unscheduled placement", () => {
    const markup = controls(ticket({ sprint_id: null, sprint_item_id: null }));
    expect(markup).toContain("Backlog ticket");
    expect(markup).toContain("Ticket actions");
    expect(markup).toContain("<details");
    expect(markup).toContain("Retire ticket");
    expect(markup).not.toContain("Resume ticket");
  });

  it("offers resume and preserves the contact and approval explanation", () => {
    const markup = controls(ticket({ is_backlogged: true, sprint_id: "scheduled" }));
    expect(markup).toContain("Resume ticket");
    expect(markup).toContain("Automatic work is paused. Messages and approvals remain available.");
  });

  it("shows escaped retirement reason and no lifecycle action on a retired ticket", () => {
    const value = ticket({ stage: "cancelled", field_values: { retirement: JSON.stringify({ reason: "Obsolete <script>" }) } });
    const markup = controls(value, true);
    expect(markup).toContain("Retired without success");
    expect(markup).toContain("Obsolete &lt;script>");
    expect(markup).not.toContain("data-ticket-backlog-action");
    expect(markup).not.toContain("data-ticket-retire-action");
    expect(controls(ticket({ stage: "done" }), true)).not.toContain("data-ticket-backlog-action");
  });

  it("does not invent a retirement reason for malformed or nonterminal provenance", () => {
    expect(retirementReason(ticket({ stage: "cancelled", field_values: { retirement: "invalid" } }))).toBeNull();
    expect(retirementReason(ticket({ field_values: { retirement: '{"reason":"old"}' } }))).toBeNull();
  });
});

describe("bounded ticket discovery", () => {
  it("sends server filters and page bounds rather than filtering one loaded page", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"tickets":[],"page":{}}', { status: 200 })));
    vi.stubGlobal("fetch", fetcher);
    const query = queries.backlogTicketSummaries(30, 30, "project one", true, "planning_day");
    if (typeof query.queryFn !== "function") throw new Error("Expected fetch function");
    await query.queryFn({ signal: new AbortController().signal } as never);
    expect(fetcher.mock.calls[0][0]).toBe("/api/tickets?detail=summary&limit=30&offset=30&project_id=project%20one&is_backlogged=true&worker_type=planning_day");
    expect(queries.backlogTicketSummaries(0).queryKey).not.toEqual(query.queryKey);
    const outside = queries.backlogTicketSummaries(0, 30, "", false);
    if (typeof outside.queryFn !== "function") throw new Error("Expected fetch function");
    await outside.queryFn({ signal: new AbortController().signal } as never);
    expect(fetcher.mock.calls[1][0]).toContain("is_backlogged=false");
    const retired = queries.backlogTicketSummaries(0, 30, "", null, "", true);
    if (typeof retired.queryFn !== "function") throw new Error("Expected fetch function");
    await retired.queryFn({ signal: new AbortController().signal } as never);
    expect(fetcher.mock.calls[2][0]).toBe("/api/tickets?detail=summary&limit=30&offset=0&include_terminal=true&stage=cancelled");
    expect(retired.queryKey).not.toEqual(queries.backlogTicketSummaries(0).queryKey);
  });
});
