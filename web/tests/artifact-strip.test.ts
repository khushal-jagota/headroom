import { describe, expect, it } from "vitest";
import {
  artifactChipIsOverflow,
  sprintItemArtifactStripItems,
  ticketArtifactStripItems
} from "../src/lib/artifactStrip";

describe("shared artifact strip", () => {
  it("sorts Item files newest first and disambiguates matching names by parent", () => {
    const rows = sprintItemArtifactStripItems("si_one", [
      { path: "old/proof.md", modified_at: 10 },
      { path: "new/proof.png", modified_at: 20 },
      { path: "notes.txt", modified_at: 15 }
    ]);
    expect(rows.map((row) => [row.path, row.label, row.kind])).toEqual([
      ["new/proof.png", "new / proof", "png"],
      ["notes.txt", "notes", "txt"],
      ["old/proof.md", "old / proof", "md"]
    ]);
  });

  it("keeps an unsafe Item path quiet instead of creating a link", () => {
    const [row] = sprintItemArtifactStripItems("si_one", [
      { path: "../escape.md", modified_at: 1 }
    ]);
    expect(row?.href).toBeNull();
  });

  it("reads the pending proposal first, then fields from latest to earliest, without duplicates", () => {
    const rows = ticketArtifactStripItems(
      ["kickoff", "success", "implementation"],
      {
        kickoff: "[old](/files/tickets/t_one/artifacts/old.md)",
        success: "[item](/files/sprint-items/si_one/artifacts/shared.pdf)",
        implementation: "[latest](/files/tickets/t_one/artifacts/latest.png) [old again](/files/tickets/t_one/artifacts/old.md)"
      },
      {
        field: "implementation",
        body: "[proposal](/files/tickets/t_one/artifacts/proposal.html)",
        proposed_by: "worker",
        created_at: 1
      }
    );
    expect(rows.map((row) => row.path)).toEqual([
      "artifacts/proposal.html",
      "artifacts/latest.png",
      "artifacts/old.md",
      "artifacts/shared.pdf"
    ]);
  });

  it("shows all six files and collapses only the seventh and later", () => {
    expect(Array.from({ length: 6 }, (_, index) => artifactChipIsOverflow(index, 6))).toEqual([
      false, false, false, false, false, false
    ]);
    expect(Array.from({ length: 7 }, (_, index) => artifactChipIsOverflow(index, 7))).toEqual([
      false, false, false, false, false, true, true
    ]);
  });

  it("extracts rendered Markdown links but ignores plain text and code", () => {
    const rows = ticketArtifactStripItems(
      ["implementation"],
      {
        implementation: [
          "/files/tickets/t_one/plain.md",
          "`[code](/files/tickets/t_one/code.md)`",
          "[linked](/files/tickets/t_one/linked.md)",
          "[reference][proof]",
          "[proof]: /files/sprint-items/si_one/artifacts/proof.png"
        ].join("\n\n")
      },
      null
    );
    expect(rows.map((row) => row.path)).toEqual([
      "linked.md",
      "artifacts/proof.png"
    ]);
  });
});
