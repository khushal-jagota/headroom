import { render } from "svelte/server";
import { describe, expect, it, vi } from "vitest";
import TicketLifecycleControls from "../src/components/TicketLifecycleControls.svelte";
import { queries } from "../src/lib/queryCatalogue";
import type { TicketDetail } from "../src/lib/types";
import { QueryClient } from "@tanstack/svelte-query";

const ticket = (overrides: Partial<TicketDetail> = {}): TicketDetail => ({
  id: "ticket-example", stage: "needs_implementation", is_backlogged: false,
  record_sha256: "fresh-record", field_values: {}, ...overrides
} as TicketDetail);

function controls(value: TicketDetail, terminal = false): string {
  return render(TicketLifecycleControls, { props: { ticket: value, terminal } }).body;
}

describe("explicit ticket lifecycle controls", () => {
  it("offers deferral independently of unscheduled placement", () => {
    const markup = controls(ticket({ sprint_item_id: null }));
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

  it("keeps resume available alongside owner and supervisor proposal facts", () => {
    for (const review of [{ awaiting_approval:true }, { awaiting_agent_approval:true }]) {
      const value = ticket({ is_backlogged:true, ...review,
        pending_proposal:{ field:"implementation", body:"Retained proposal", proposed_by:"worker", created_at:1, revision:3 } });
      expect(controls(value)).toContain("Resume ticket");
    }
    expect(controls(ticket({ stage:"done", is_backlogged:true }), true)).not.toContain("data-ticket-backlog-action");
  });
});

describe("bounded orchestrator ticket discovery", () => {
  it("keeps different principals, filters, and pages in independent cached reads", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    vi.stubGlobal("fetch", async (url: string) => {
      const parsed = new URL(url, "https://panels.invalid");
      const id = parsed.searchParams.get("orchestrator_id") ?? "chief";
      const filter = parsed.searchParams.get("is_backlogged") === "true" ? "backlogged" : "active";
      const offset = parsed.searchParams.get("offset");
      return new Response(JSON.stringify({ tickets: [{ id: `${id}-${filter}-${offset}` }], page: {} }), { status: 200 });
    });
    try {
      const principal = { kind: "sprint_item", id: "item one" } as const;
      const chief = queries.orchestratorTicketSummaries({ kind: "chief", id: "chief" }, "active", 0);
      const item = queries.orchestratorTicketSummaries(principal, "active", 0);
      const backlog = queries.orchestratorTicketSummaries(principal, "backlogged", 0);
      const next = queries.orchestratorTicketSummaries(principal, "active", 30);
      for (const query of [chief, item, backlog, next]) await client.fetchQuery(query);
      expect(client.getQueryData(chief.queryKey)).toMatchObject({ body: { tickets: [{ id: "chief-active-0" }] } });
      expect(client.getQueryData(item.queryKey)).toMatchObject({ body: { tickets: [{ id: "item one-active-0" }] } });
      expect(client.getQueryData(backlog.queryKey)).toMatchObject({ body: { tickets: [{ id: "item one-backlogged-0" }] } });
      expect(client.getQueryData(next.queryKey)).toMatchObject({ body: { tickets: [{ id: "item one-active-30" }] } });
    } finally {
      client.clear();
      vi.unstubAllGlobals();
    }
  });

  it("isolates ownership, filter and page bounds in the query and request", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"tickets":[],"page":{}}', { status: 200 })));
    vi.stubGlobal("fetch", fetcher);
    try {
      const chief = { kind: "chief", id: "chief" } as const;
      const item = { kind: "sprint_item", id: "item one" } as const;
      for (const [principal, filter, offset, suffix] of [
        [chief, "active", 0, "&orchestrator_kind=chief&is_backlogged=false"],
        [item, "backlogged", 30, "&orchestrator_kind=sprint_item&orchestrator_id=item%20one&is_backlogged=true"],
        [chief, "done", 0, "&orchestrator_kind=chief&include_terminal=true&stage=done"]
      ] as const) {
        const query = queries.orchestratorTicketSummaries(principal, filter, offset);
        if (typeof query.queryFn !== "function") throw new Error("Expected fetch function");
        await query.queryFn({ signal: new AbortController().signal } as never);
        expect(fetcher.mock.calls.at(-1)?.[0]).toBe(`/api/tickets?detail=summary&limit=30&offset=${offset}${suffix}`);
      }
    } finally { vi.unstubAllGlobals(); }
  });
});
