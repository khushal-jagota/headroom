import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import PriorityTile from "../src/components/PriorityTile.svelte";

function priorityTile(priority: string, decorative = false): string {
  return render(PriorityTile, { props: { priority, decorative } }).body;
}

describe("priority tile", () => {
  it("translates only the visible P0 label", () => {
    const p0 = priorityTile("P0");

    expect(p0).toContain('class="priority-tile priority-tile--p0"');
    expect(p0).toContain('data-priority-tile="P0"');
    expect(p0).toContain('aria-label="Priority P0"');
    expect(p0).toContain(">!!!</span>");
    expect(p0).not.toContain(">P0</span>");

    for (const priority of ["P1", "P2", "P3"]) {
      expect(priorityTile(priority)).toContain(`>${priority}</span>`);
    }
  });

  it("keeps decorative tiles silent", () => {
    const decorative = priorityTile("P0", true);

    expect(decorative).toContain('aria-hidden="true"');
    expect(decorative).not.toContain('role="img"');
    expect(decorative).not.toContain("aria-label=");
    expect(decorative).toContain(">!!!</span>");
  });
});
