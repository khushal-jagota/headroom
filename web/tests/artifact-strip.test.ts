import { describe, expect, it } from "vitest";
import {
  artifactChipIsOverflow,
  sprintItemArtifactStripItems,
  ticketArtifactStripItems
} from "../src/lib/artifactStrip";

describe("shared artifact strip", () => {
  it("keeps an unsafe Item path quiet instead of creating a link", () => {
    const [row] = sprintItemArtifactStripItems("si_one", [
      { name: "escape.md", opens: "../escape.md", modified_at: 1, children: [] }
    ]);
    expect(row?.href).toBeNull();
  });

  it("reads the pending proposal first, then fields from latest to earliest, without duplicates", () => {
    const rows = ticketArtifactStripItems(
      ["brief", "success", "implementation"],
      {
        brief: "[old](/files/tickets/t_one/artifacts/old.md)",
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
