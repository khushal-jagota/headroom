import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/svelte-query";
import { queries } from "../src/lib/queryCatalogue";

afterEach(() => vi.unstubAllGlobals());

it("keeps machine health reads independent in the query cache", async () => {
  // Single-executor tests cannot catch a shared key that returns another destination's health.
  const client = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: Infinity}}});
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (input: string) => {
    const machine = new URL(input, "https://panels.invalid").searchParams.get("machine_id");
    calls.push(machine ?? "missing");
    return new Response(JSON.stringify({mode: "remote", presence: machine === "awake" ? "present" : "asleep"}),
      {headers: {"content-type": "application/json"}});
  });
  try {
    const awake = await client.fetchQuery(queries.executorHealth("awake"));
    const asleep = await client.fetchQuery(queries.executorHealth("asleep"));
    const awakeAgain = await client.fetchQuery(queries.executorHealth("awake"));
    expect(awake.body.presence).toBe("present");
    expect(asleep.body.presence).toBe("asleep");
    expect(awakeAgain.body.presence).toBe("present");
    expect(calls).toEqual(["awake", "asleep"]);
  } finally { client.clear(); }
});
