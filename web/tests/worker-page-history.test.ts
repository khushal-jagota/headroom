import { describe, expect, it } from "vitest";
import { buildLifecycle } from "../src/lib/lifecycle";
import { workerPageHistory } from "../src/lib/workerPageHistory";
import type { TicketDetail } from "../src/lib/types";
const lifecycle = buildLifecycle({ worker_type:"coding", label:"Coding", stages:[
  { id:"needs_brief", label:"Brief", gating_field:"brief", is_terminal:false, ownership_mode:"worker" },
  { id:"needs_plan", label:"Plan", gating_field:"plan", is_terminal:false, ownership_mode:"worker" },
  { id:"needs_implementation", label:"Implementation", gating_field:"implementation", is_terminal:false, ownership_mode:"worker" },
  { id:"done", label:"Done", gating_field:null, is_terminal:true, ownership_mode:null }
], fields:[{ id:"brief", label:"Brief" },{ id:"plan", label:"Plan" },{ id:"implementation", label:"Implementation" },{ id:"retirement", label:"Retirement" }], advance:{}, ceiling_range:[], default_ceiling:"needs_plan", worker_profile_id:"coding", default_backend:"codex", default_model:null, default_reasoning_effort:null });
const ticket = (stage:string, field_values:Record<string,string> = {}): TicketDetail => ({ stage, field_values } as TicketDetail);
describe("Worker page occurred steps", () => {
  it("includes completed/current steps and excludes future steps and provenance", () => {
    expect(workerPageHistory(lifecycle,ticket("needs_plan"))).toMatchObject({ fields:["brief","plan"], completed:["brief"], historicalProposal:null });
  });
  it("reads cancelled last real stage and proposal without false completion", () => {
    const history = workerPageHistory(lifecycle,ticket("cancelled", { brief:"Approved", retirement:JSON.stringify({ original_stage:"needs_plan", original_pending_proposal_json:JSON.stringify({ field:"plan", body:"Historical pending" }) }) }));
    expect(history).toEqual({ stage:"needs_plan", fields:["brief","plan"], completed:["brief"], historicalProposal:{ field:"plan", body:"Historical pending" } });
  });
  it("keeps saved prose accessible when cancellation provenance cannot be read", () => {
    expect(workerPageHistory(lifecycle,ticket("cancelled", { brief:"Saved", retirement:"bad" }))).toMatchObject({ fields:["brief"], completed:[], historicalProposal:null });
  });
});
