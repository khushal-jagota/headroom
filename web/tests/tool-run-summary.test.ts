import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import type { ToolCallPresentation, ToolRunSummary as Summary } from "../src/lib/conversation/toolCallPresentation";

const closed: Summary = { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: [] };
const running = { title: "Running", summary: "go test -race ./internal/push/...", summaryIsLiteral: true, sentence: "Running go test" } as unknown as ToolCallPresentation;
const draw = (summary: Summary, callCount: number): string => render(ToolRunSummary, { props: { summary, callCount, onToggle: () => { } } }).body;

describe("a run of tool calls in Full", () => {
  it("closed: says the sentence, then the chevron, with no empty status slot between them", () => {
    const html = draw(closed, 2);
    expect(html.indexOf("Ran 2 commands")).toBeLessThan(html.indexOf("c2-run-chevron"));
    expect(html).not.toContain("data-conversation-work-status");
  });

  it("says a failure as part of the label, before the chevron", () => {
    const html = draw({ ...closed, failedCount: 1 }, 2);
    expect(html.indexOf("1 failed")).toBeLessThan(html.indexOf("c2-run-chevron"));
  });

  it("running: says Running, then the command set as code, then the chevron at the row's end", () => {
    const html = draw({ ...closed, running }, 3);
    const verb = html.indexOf(">Running<");
    const command = html.indexOf("go test -race");
    const chevron = html.indexOf("c2-run-chevron");
    expect(verb).toBeGreaterThan(-1);
    expect(verb).toBeLessThan(command);
    expect(command).toBeLessThan(chevron);
    expect(html).toMatch(/class="c2-run-subject[^"]*c2-run-subject--literal/);
  });

  it("keeps its icon while a call runs, marked running, with no spinner (only the Working line has one)", () => {
    const html = draw({ ...closed, running, iconPaths: ["M1 1"] }, 3);
    expect(html).toMatch(/<svg class="c2-run-icon[^"]*"[^>]*data-conversation-run-icon="running"/);
    expect(html).not.toContain("spinner");
  });

  it("keeps its icon once settled, not marked running", () => {
    const html = draw({ ...closed, iconPaths: ["M1 1"] }, 2);
    expect(html).toContain("c2-run-icon");
    expect(html).not.toContain('data-conversation-run-icon="running"');
  });
});
