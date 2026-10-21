import { expect, it } from "vitest";
import { ticketPageArtifactItems } from "../src/lib/artifactStrip";
import { targetFromHref, targetFromPreviewHref, previewHashHref, managerFileHref } from "../src/lib/filePreview";
import type { ManagerArtifact } from "../src/lib/types";

it("lists unlinked managed artifacts, folds folders and deduplicates linked targets at every depth", () => {
  const entries: ManagerArtifact[] = [
    {name:"site",opens:"artifacts/site/index.html",modified_at:1,children:[]},
    {name:"notes",opens:null,modified_at:1,children:[{name:"unlinked.md",opens:"artifacts/notes/unlinked.md",modified_at:1,children:[]}]}
  ];
  const items = ticketPageArtifactItems("t_owner",entries,["plan"],{plan:
    "[site](/files/tickets/t_owner/artifacts/site/index.html) [note](/files/tickets/t_owner/artifacts/notes/unlinked.md) [other](/files/tickets/t_other/artifacts/result.html) [item](/files/managers/si_parent/artifacts/brief.md)"},null);
  expect(items.map((item)=>item.label)).toEqual(["site","notes","result","brief"]);
  expect(items[0].target).toEqual({kind:"ticket-file",ticketId:"t_owner",path:"artifacts/site/index.html"});
  expect(items[1].children[0].target).toEqual({kind:"ticket-file",ticketId:"t_owner",path:"artifacts/notes/unlinked.md"});
  expect(items.at(-1)?.target?.kind).toBe("manager-file");
});

it("keeps legacy linked managed artifacts when optional canonical listing is absent", () => {
  const items = ticketPageArtifactItems("t_owner",undefined,["plan"],{plan:"[old](/files/tickets/t_owner/old.md)"},null);
  expect(items).toHaveLength(1);
  expect(items[0].path).toBe("old.md");
});

it("decodes retained Manager file addresses and emits the same canonical target", () => {
  const expected = {kind:"manager-file",managerId:"si_existing",path:"artifacts/plan.md"} as const;
  expect(targetFromHref("/files/sprint-items/si_existing/artifacts/plan.md")).toEqual(expected);
  expect(targetFromPreviewHref("#/preview?source=sprint-item&item=si_existing&path=artifacts%2Fplan.md")).toEqual(expected);
  expect(targetFromPreviewHref("#/preview?source=manager&manager=si_existing&path=artifacts%2Fplan.md")).toEqual(expected);
  expect(managerFileHref(expected)).toBe("/files/managers/si_existing/artifacts/plan.md");
  expect(previewHashHref(expected)).toBe("#/preview?source=manager&manager=si_existing&path=artifacts%2Fplan.md");
});
