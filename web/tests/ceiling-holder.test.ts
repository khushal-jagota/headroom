import { describe, expect, it } from "vitest";
import { holderLabelFor, holderOptionsFor } from "../src/lib/ceilingHolder";
const parent = { id:"si_parent", title:"Actual outcome title" };
describe("holder role wording and identity", () => {
  it("uses approved roles with the assigned manager identity as detail", () => {
    expect(holderOptionsFor(parent)).toEqual([
      { value:"owner", label:"User" }, { value:"chief", label:"Chief" },
      { value:"si_parent", label:"Manager", detail:"Actual outcome title" }
    ]);
    expect(holderLabelFor(parent, {kind:"manager", id:"si_parent"})).toBe("Manager");
  });
  it("keeps foreign managers and Worker holders distinguishable", () => {
    expect(holderLabelFor(parent, {kind:"manager", id:"si_other"})).toBe("Manager · si_other");
    expect(holderLabelFor(parent, {kind:"ticket", id:"t_other"})).toBe("Worker · t_other");
    expect(holderOptionsFor(parent, {kind:"manager", id:"si_other"}).at(-1)).toEqual({value:"si_other", label:"Manager", detail:"si_other"});
  });
});
