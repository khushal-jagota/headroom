import { describe, expect, it } from "vitest";
import { buildLifecycle, ceilingOptionsFor } from "../src/lib/lifecycle";
import { ceilingLabelFor } from "../src/lib/ceilingPresentation";
const lifecycle = buildLifecycle({worker_type:"coding",label:"Coding",stages:[
  {id:"needs_success_condition",label:"Success Condition",gating_field:"success_condition",is_terminal:false,ownership_mode:"worker"},
  {id:"needs_plan",label:"Plan",gating_field:"plan",is_terminal:false,ownership_mode:"worker"},
  {id:"done",label:"Done",gating_field:null,is_terminal:true,ownership_mode:null}
],fields:[],advance:{needs_success_condition:"needs_plan",needs_plan:"done"},ceiling_range:["needs_success_condition","needs_plan","done"],default_ceiling:"needs_plan",worker_profile_id:"coding",default_backend:"codex",default_model:null,default_reasoning_effort:null});
describe("saved ceiling presentation", () => {
  it("names a lower saved ceiling without adding it to the current choice floor", () => {
    expect(ceilingOptionsFor(lifecycle,"needs_plan").map((option)=>option.value)).toEqual(["needs_plan","done"]);
    expect(ceilingLabelFor(lifecycle,"needs_success_condition")).toBe("Success Condition");
  });
  it("names the no-further sentinel and retains the unchosen placeholder", () => {
    expect(ceilingLabelFor(lifecycle,"none")).toBe("No further");
    expect(ceilingLabelFor(lifecycle,null)).toBe("Choose ceiling");
  });
});
