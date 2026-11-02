/** One rule for the press that opens an artifact where it was named.
 *
 * Every pane that shows a conversation used to carry its own copy of this, and the copies
 * disagreed: a Manager's pane claimed a press on a `.zip` and on a link marked as a
 * download, where the Ticket screen left both to the browser. The rule is one function
 * now, and these are its cases.
 */
import { describe, expect, it } from "vitest";
import { artifactForPressedLink } from "../src/lib/managedArtifactPress";

const press = {} as const;
const link = (href: string | null, extra: { target?: string; isDownload?: boolean } = {}) => ({
  href,
  target: extra.target ?? null,
  isDownload: extra.isDownload ?? false
});

describe("the press that opens an artifact where it was named", () => {
  it("takes a preview address, which exists to ask for the viewer", () => {
    expect(
      artifactForPressedLink(link("#/preview?source=ticket&ticket=t_1&path=notes%2Fplan.md"), press)
    ).toEqual({ kind: "ticket-file", ticketId: "t_1", path: "notes/plan.md" });
  });

  it("takes a managed link whose format has something to show", () => {
    expect(artifactForPressedLink(link("/files/tickets/t_1/plan.html"), press)).toEqual({
      kind: "ticket-file",
      ticketId: "t_1",
      path: "plan.html"
    });
  });

  it("opens goal catalog and conversation links with the shared preview rules", () => {
    const target = { kind: "goal-file", goalId: "goal_1", path: "artifacts/field guide.md" };
    expect(artifactForPressedLink(link("/files/goals/goal_1/artifacts/field%20guide.md"), press)).toEqual(target);
    expect(artifactForPressedLink(link("#/preview?source=goal&goal=goal_1&path=artifacts%2Ffield%20guide.md"), press)).toEqual(target);
    expect(artifactForPressedLink(link("/files/goals/goal_1/artifacts/archive.zip"), press)).toBeNull();
  });

  it("leaves a managed file with nothing to preview as the download it is", () => {
    expect(artifactForPressedLink(link("/files/managers/si_1/archive.zip"), press)).toBeNull();
  });

  it("takes a preview address even for a file the viewer can only offer", () => {
    expect(
      artifactForPressedLink(link("#/preview?source=ticket&ticket=t_1&path=receipt.json"), press)
    ).toEqual({ kind: "ticket-file", ticketId: "t_1", path: "receipt.json" });
  });

  it("leaves a link the writer marked as a download", () => {
    expect(
      artifactForPressedLink(link("/files/tickets/t_1/plan.md", { isDownload: true }), press)
    ).toBeNull();
  });

  it("leaves a press asking for another tab, and one asking for a new window", () => {
    expect(artifactForPressedLink(link("/files/tickets/t_1/plan.md", { target: "_blank" }), press))
      .toBeNull();
    expect(artifactForPressedLink(link("/files/tickets/t_1/plan.md"), { metaKey: true })).toBeNull();
    expect(artifactForPressedLink(link("/files/tickets/t_1/plan.md"), { button: 1 })).toBeNull();
  });

  it("leaves every other address alone", () => {
    expect(artifactForPressedLink(link("#/workspace/t_1"), press)).toBeNull();
    expect(artifactForPressedLink(link("https://example.test/plan.md"), press)).toBeNull();
    expect(artifactForPressedLink(link("/dev/tickets/t_1/8791/"), press)).toBeNull();
    expect(artifactForPressedLink(link(null), press)).toBeNull();
  });
});
