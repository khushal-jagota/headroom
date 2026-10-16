import { expect, it } from "vitest";
import { ticketPageArtifactItems } from "../src/lib/artifactStrip";
import type { SprintItemArtifact } from "../src/lib/types";

it("lists unlinked managed artifacts, folds folders and deduplicates linked targets at every depth", () => {
  const entries: SprintItemArtifact[] = [
    {name:"site",opens:"artifacts/site/index.html",modified_at:1,children:[]},
    {name:"notes",opens:null,modified_at:1,children:[{name:"unlinked.md",opens:"artifacts/notes/unlinked.md",modified_at:1,children:[]}]}
  ];
  const items = ticketPageArtifactItems("t_owner",entries,["plan"],{plan:
    "[site](/files/tickets/t_owner/artifacts/site/index.html) [note](/files/tickets/t_owner/artifacts/notes/unlinked.md) [other](/files/tickets/t_other/artifacts/result.html) [item](/files/sprint-items/si_parent/artifacts/brief.md)"},null);
  expect(items.map((item)=>item.label)).toEqual(["site","notes","result","brief"]);
  expect(items[0].target).toEqual({kind:"ticket-file",ticketId:"t_owner",path:"artifacts/site/index.html"});
  expect(items[1].children[0].target).toEqual({kind:"ticket-file",ticketId:"t_owner",path:"artifacts/notes/unlinked.md"});
  expect(items.at(-1)?.target?.kind).toBe("sprint-item-file");
});

it("keeps legacy linked managed artifacts when optional canonical listing is absent", () => {
  const items = ticketPageArtifactItems("t_owner",undefined,["plan"],{plan:"[old](/files/tickets/t_owner/old.md)"},null);
  expect(items).toHaveLength(1);
  expect(items[0].path).toBe("old.md");
});
