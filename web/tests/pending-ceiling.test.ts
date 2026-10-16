import { describe, expect, it } from "vitest";
import { buildLifecycle } from "../src/lib/lifecycle";
import { pendingCeilingApproves, pendingCeilingEffect } from "../src/lib/pendingCeiling";
const lifecycle = buildLifecycle({ worker_type:"coding", label:"Coding", stages:[
  {id:"brief",label:"Brief",gating_field:"brief",is_terminal:false,ownership_mode:"worker"},
  {id:"plan",label:"Plan",gating_field:"plan",is_terminal:false,ownership_mode:"worker"},
  {id:"implementation",label:"Implementation",gating_field:"implementation",is_terminal:false,ownership_mode:"worker"},
  {id:"done",label:"Done",gating_field:null,is_terminal:true,ownership_mode:null}
], fields:[], advance:{brief:"plan",plan:"implementation",implementation:"done"}, ceiling_range:["brief","plan","implementation","done"], default_ceiling:"plan",worker_profile_id:"coding",default_backend:"codex",default_model:null,default_reasoning_effort:null });
describe("pending ceiling approval intent", () => {
  it.each([
    ["plan","implementation",true,"scope",true],
    ["plan","plan",true,"scope",false],
    ["plan","brief",true,"scope",false],
    ["implementation","implementation",true,"scope",false],
    ["done","implementation",true,"scope",false],
    ["implementation","implementation",false,"scope",false],
    ["implementation","done",true,"scope",true],
    ["done","implementation",false,"approve",true],
    ["done","plan",true,"approve",false],
    ["done","brief",true,"approve",false]
  ] as const)("saved %s selected %s touched %s intent %s approves %s", (saved,selected,touched,intent,expected) => {
    expect(pendingCeilingApproves(lifecycle,"plan",saved,selected,touched,intent)).toBe(expected);
  });
  it("previews reviewer-only reroute with no approval under already-higher permission", () => {
    expect(pendingCeilingEffect(lifecycle,"plan","Plan","done","done",false,"scope",true,"Orchestrator")).toEqual({approves:false,text:"Sends this Plan proposal to Orchestrator. Nothing is approved."});
  });
  it("previews changed stage approval and truthful same/lower scope", () => {
    expect(pendingCeilingEffect(lifecycle,"plan","Plan","plan","implementation",true,"scope",false,"User")).toEqual({approves:true,text:"Approves this Plan proposal and carries on to Implementation."});
    expect(pendingCeilingEffect(lifecycle,"plan","Plan","done","implementation",true,"scope",false,"User")).toEqual({approves:false,text:"Nothing is approved while the ceiling stays at Implementation."});
  });
});
