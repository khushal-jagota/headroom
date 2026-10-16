import { describe, expect, it } from "vitest";
import { holderFromValue, holderLabelFor, holderOptionsFor, holderValue } from "../src/lib/ceilingHolder";
const parent = { id:"si_parent", title:"Actual outcome title" };
describe("holder role wording and identity", () => {
  it("uses approved roles with the assigned orchestrator identity as detail", () => {
    expect(holderOptionsFor(parent)).toEqual([
      { value:"owner", label:"User" }, { value:"chief", label:"Chief" },
      { value:"si_parent", label:"Orchestrator", detail:"Actual outcome title" }
    ]);
    expect(holderLabelFor(parent, {kind:"sprint_item", id:"si_parent"})).toBe("Orchestrator");
  });
  it("keeps foreign orchestrators and Worker holders distinguishable", () => {
    expect(holderLabelFor(parent, {kind:"sprint_item", id:"si_other"})).toBe("Orchestrator · si_other");
    expect(holderLabelFor(parent, {kind:"ticket", id:"t_other"})).toBe("Worker · t_other");
    expect(holderOptionsFor(parent, {kind:"sprint_item", id:"si_other"}).at(-1)).toEqual({value:"si_other", label:"Orchestrator", detail:"si_other"});
  });
  it("changes words without changing selected principal values", () => {
    for (const value of ["owner","chief","si_parent","si_other","t_other"]) {
      expect(holderValue(holderFromValue(value,parent))).toBe(value);
    }
    expect(holderLabelFor(parent,null)).toBe("User");
  });
});
