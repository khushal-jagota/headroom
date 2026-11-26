/** Distinct risk: a machine choice must reach every provider operation. Existing sign-in
 * tests exercise ordering for one backend only. No browser or provider is needed. */
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/svelte-query";
import { queries } from "../src/lib/queryCatalogue";
import { readBackends, refreshBackends, startBackendSignIn, readBackendSignIn,
  cancelBackendSignIn, submitBackendSignInReturnUrl } from "../src/lib/conversation/wire";

afterEach(() => vi.unstubAllGlobals());

it("routes cards, allowance refresh and the whole sign-in to the chosen machine", async () => {
  const fetch = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({backends: [], sign_in: null}), {status: 200}));
  vi.stubGlobal("fetch", fetch);
  const machine = "machine two";
  await readBackends(true, machine);
  await refreshBackends(machine);
  await startBackendSignIn("codex", machine);
  await readBackendSignIn("codex", machine);
  await cancelBackendSignIn("codex", "session/one", machine);
  await submitBackendSignInReturnUrl("codex", "session/one", "http://return.test/", machine);
  const paths = fetch.mock.calls.map(call => new URL(String(call[0]), "https://panels.test"));
  expect(paths.map(path => path.searchParams.get("machine_id"))).toEqual(Array(6).fill(machine));
  expect(paths[0].searchParams.get("refresh")).toBe("true");
  expect(paths[4].pathname).toContain("session%2Fone");
  expect(paths[5].pathname).toContain("session%2Fone/return-url");
});

// Distinct risk: discovery writes used to refetch their own mounted picker. Existing
// coordinator tests do not cover the asynchronous backend route or one-shot discovery.
it("keeps picker versions with their body and consumes explicit discovery once", async () => {
  const { QueryObserver } = await import("@tanstack/svelte-query");
  const { createResourceCoordinator } = await import("../src/lib/resourceVersions/coordinator");
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  const homeKey = "/api/conversation/backends";
  const ticketKey = homeKey + "?ticket_id=ticket%20two";
  let ticketRevision = "ticket-one";
  const reads: {ticket: boolean; refresh: boolean}[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "https://panels.test");
    if (url.pathname === "/api/resource-versions") return new Response(JSON.stringify({
      epoch: "server", resources: JSON.parse(url.searchParams.get("keys")!).map((key: string) =>
        ({key, revision: key === homeKey ? "home-one" : ticketRevision}))
    }));
    const ticket = init?.method === "POST";
    if (ticket) expect(JSON.parse(String(init?.body))).toEqual({ticket_id: "ticket two"});
    reads.push({ticket, refresh: url.searchParams.get("refresh") === "true"});
    expect(url.searchParams.get("versioned")).toBe("true");
    return new Response(JSON.stringify({epoch: "server", key: ticket ? ticketKey : homeKey,
      revision: ticket ? ticketRevision : "home-one",
      body: {backends: [{backend_key: ticket ? "ticket-provider" : "home-provider"}]}}));
  }));
  const options = queries.ticketBackends("ticket two");
  const home = new QueryObserver(client, queries.backends());
  const ticket = new QueryObserver(client, options);
  const stopHome = home.subscribe(() => {});
  const stopTicket = ticket.subscribe(() => {});
  const coordinator = createResourceCoordinator(client);
  try {
    await Promise.all([home.refetch(), ticket.refetch()]);
    expect(home.getCurrentResult().data).toEqual([{backend_key: "home-provider"}]);
    expect(ticket.getCurrentResult().data).toEqual([{backend_key: "ticket-provider"}]);
    expect(client.getQueryData(options.queryKey)).toMatchObject({
      epoch: "server", revision: "ticket-one", body: [{backend_key: "ticket-provider"}]
    });
    reads.length = 0;
    await coordinator.refresh();
    expect(reads).toEqual([]);
    ticketRevision = "ticket-two";
    await coordinator.refresh();
    expect(reads).toEqual([{ticket: true, refresh: false}]);
    reads.length = 0;
    await client.fetchQuery({...queries.ticketBackends("ticket two", [], true), staleTime: 0});
    await client.invalidateQueries({queryKey: options.queryKey});
    expect(reads).toEqual([{ticket: true, refresh: true}, {ticket: true, refresh: false}]);
    await coordinator.refresh();
    expect(reads).toHaveLength(2);
  } finally {
    stopHome(); stopTicket(); coordinator.stop(); client.clear();
  }
});
