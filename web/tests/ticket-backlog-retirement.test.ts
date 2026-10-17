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

describe("bounded orchestrator ticket discovery", () => {
  it("isolates ownership, filter and page bounds in the query and request", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"tickets":[],"page":{}}', { status: 200 })));
    vi.stubGlobal("fetch", fetcher);
    try {
      const chief = { kind: "chief", id: "chief" } as const;
      const item = { kind: "sprint_item", id: "item one" } as const;
      for (const [principal, filter, offset, suffix] of [
        [chief, "active", 0, "&orchestrator_kind=chief"],
        [item, "backlogged", 30, "&orchestrator_kind=sprint_item&orchestrator_id=item%20one&is_backlogged=true"],
        [chief, "done", 0, "&orchestrator_kind=chief&include_terminal=true&stage=done"],
        [chief, "retired", 0, "&orchestrator_kind=chief&include_terminal=true&stage=cancelled"]
      ] as const) {
        const query = queries.orchestratorTicketSummaries(principal, filter, offset);
        if (typeof query.queryFn !== "function") throw new Error("Expected fetch function");
        await query.queryFn({ signal: new AbortController().signal } as never);
        expect(fetcher.mock.calls.at(-1)?.[0]).toBe(`/api/tickets?detail=summary&limit=30&offset=${offset}${suffix}`);
      }
      const key = queries.orchestratorTicketSummaries(chief, "active", 0).queryKey;
      for (const query of [queries.orchestratorTicketSummaries(item, "active", 0), queries.orchestratorTicketSummaries(chief, "backlogged", 0), queries.orchestratorTicketSummaries(chief, "active", 30), queries.orchestratorTicketSummaries(chief, "active", 0, 15)]) {
        expect(query.queryKey).not.toEqual(key);
      }
    } finally { vi.unstubAllGlobals(); }
  });
});
