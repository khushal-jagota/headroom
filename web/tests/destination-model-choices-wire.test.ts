/** A read must carry absent and explicitly cleared effort as different requests. */
import { afterEach, expect, it, vi } from "vitest";
import { readScopedBackends } from "../src/lib/conversation/wire";

afterEach(() => vi.unstubAllGlobals());

it("posts scoped preview without converting omitted effort into provider default", async () => {
  const answer = {backends: []};
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(answer)));
  vi.stubGlobal("fetch", fetcher);
  const selection = { ticket_id: "t_preview", locale: "macos" as const };
  expect(await readScopedBackends(selection)).toEqual(answer.backends);
  expect(fetcher.mock.calls[0]).toEqual([
    "/api/conversation/backends",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) }
  ]);
  await readScopedBackends({ ...selection, employee_launch_reasoning_effort: null });
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    body: JSON.stringify({ ...selection, employee_launch_reasoning_effort: null })
  });
});
