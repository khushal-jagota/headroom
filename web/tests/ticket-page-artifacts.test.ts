import { expect, it } from "vitest";
import { ticketPageArtifactItems } from "../src/lib/artifactStrip";
import { targetFromHref, targetFromPreviewHref, previewHashHref, managerFileHref, goalFileHref, resolvePreview } from "../src/lib/filePreview";
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


it("round trips goal files and uses ordinary managed formats", () => {
  const target = {kind:"goal-file",goalId:"goal_existing",path:"artifacts/field guide #1?.md"} as const;
  expect(targetFromHref(goalFileHref(target))).toEqual(target);
  expect(targetFromPreviewHref(previewHashHref(target))).toEqual(target);
  for (const [extension, kind] of [["md", "markdown"], ["html", "html"], ["png", "image"], ["mp4", "video"], ["mp3", "audio"], ["zip", "download"]]) {
    expect(resolvePreview({...target,path:`artifacts/file.${extension}`}).kind).toBe(kind);
  }
  const items = ticketPageArtifactItems("t_owner",undefined,["plan"],{plan:
    `[goal](${goalFileHref(target)}) [same](${previewHashHref(target)})`},null);
  expect(items).toHaveLength(1);
  expect(items[0].key).toBe(`goal:goal_existing:${target.path}`);
  expect(items[0].target).toEqual(target);
});

it("refuses unsafe goal file paths and foreign origins before previewing them", () => {
  for (const href of [
    "/files/goals/goal_1/artifacts/../secret.md",
    "/files/goals/goal_1/artifacts/%252e%252e/secret.md",
    "/files/goals/goal_1/artifacts/%5csecret.md",
    "https://other.test/files/goals/goal_1/artifacts/note.md"
  ]) expect(targetFromHref(href).kind).toBe("external-link");
  expect(targetFromPreviewHref("#/preview?source=goal&goal=goal_1&path=..%2Fsecret.md")).toBeNull();
  expect(() => resolvePreview({kind:"goal-file",goalId:"goal_1",path:"../secret.md"})).toThrow("unsafe goal file target");
});
