/** A read must carry absent and explicitly cleared effort as different requests. */
import { afterEach, expect, it, vi } from "vitest";
import { readDestinationModelChoices } from "../src/lib/conversation/wire";
import type { DestinationModelChoicesResponse } from "../src/lib/conversation/modelResolutionWire";

afterEach(() => vi.unstubAllGlobals());

it("posts scoped preview without converting omitted effort into provider default", async () => {
  const answer: DestinationModelChoicesResponse = {
    validation: {
      selection: {
        destination: "macos", backend_key: "codex", submitted_token: "latest",
        selection_kind: "provider_alias", reasoning_effort: "high", selection_revision: "3"
      },
      state: "unresolved", reason: "offline", corrective_action: "reconnect_destination",
      resolved_model_id: null, launch_token: null, evidence: null,
      current_alias_resolution: null, previous_alias_resolution: null, unavailable: null
    },
    rows: []
  };
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(answer)));
  vi.stubGlobal("fetch", fetcher);
  const selection = { ticket_id: "t_preview", locale: "macos" as const };
  expect(await readDestinationModelChoices(selection)).toEqual(answer);
  expect(fetcher.mock.calls[0]).toEqual([
    "/api/conversation/backends/model-choices",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) }
  ]);
  await readDestinationModelChoices({ ...selection, employee_launch_reasoning_effort: null });
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    body: JSON.stringify({ ...selection, employee_launch_reasoning_effort: null })
  });
});
