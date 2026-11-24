/** Distinct risk: a machine choice must reach every provider operation, and allowance
 * cache entries must never substitute another machine's account. Existing sign-in
 * tests exercise ordering for one backend only. No browser or provider is needed. */
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/svelte-query";
import { queries } from "../src/lib/queryCatalogue";
import { readBackends, refreshBackends, startBackendSignIn, readBackendSignIn,
  cancelBackendSignIn, submitBackendSignInReturnUrl } from "../src/lib/conversation/wire";
import { readUsageLimits } from "../src/lib/conversation/recordedUsageWire";

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
  await readUsageLimits(machine);
  const paths = fetch.mock.calls.map(call => new URL(String(call[0]), "https://panels.test"));
  expect(paths.map(path => path.searchParams.get("machine_id"))).toEqual(Array(7).fill(machine));
  expect(paths[0].searchParams.get("refresh")).toBe("true");
  expect(paths[4].pathname).toContain("session%2Fone");
  expect(paths[5].pathname).toContain("session%2Fone/return-url");
});

it("retains the first machine's allowance without showing it when the second machine fails", async () => {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  const first = queries.usageLimits("first");
  const second = queries.usageLimits("second");
  client.setQueryData(first.queryKey, {codex: {checkedAt: "2026-11-22T12:00:00Z", windows: []}});
  const fetch = vi.fn(async (_input: RequestInfo | URL) => new Response("unavailable", {status: 503}));
  vi.stubGlobal("fetch", fetch);
  await expect(client.fetchQuery(second)).rejects.toThrow();
  expect(client.getQueryData(second.queryKey)).toBeUndefined();
  expect(client.getQueryData(first.queryKey)).toHaveProperty("codex");
  client.clear();
});
