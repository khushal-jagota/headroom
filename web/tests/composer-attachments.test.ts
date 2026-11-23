import { describe, expect, it } from "vitest";
import { attachmentByteLabel, attachmentDisplayName, attachmentKindLabel } from "../src/components/conversation/composer/attachments";

describe("a pending attachment tile", () => {
  it("cuts a long name in its middle so its start and kind survive", () => {
    expect(attachmentDisplayName("ci-run-4812.txt")).toBe("ci-run-4812.txt");
    const cut = attachmentDisplayName("export-weekly-retention-cohorts-2026-11-v1.csv");
    expect(Array.from(cut).length).toBeLessThanOrEqual(26);
    expect(cut).toContain("…");
    expect(cut.startsWith("export-")).toBe(true);
    expect(cut.endsWith("v1.csv")).toBe(true);
  });

  it("writes its size in the sent file card's units", () => {
    expect(attachmentByteLabel(692)).toBe("692 B");
    expect(attachmentByteLabel(1741)).toBe("1.7 KiB");
    expect(attachmentByteLabel(24 * 1024)).toBe("24 KiB");
    expect(attachmentByteLabel(3.1 * 1024 * 1024)).toBe("3.1 MiB");
  });

  it("names its kind in words as people do, never its raw media type", () => {
    expect(attachmentKindLabel("export.csv", "text/csv")).toBe("CSV");
    expect(attachmentKindLabel("chart.png", "image/png")).toBe("PNG");
    expect(attachmentKindLabel("ci-run-4812.txt", "text/plain")).toBe("Text");
    expect(attachmentKindLabel("data.json", "application/json")).toBe("JSON");
    expect(attachmentKindLabel("spec.pdf", "application/pdf")).toBe("PDF");
    expect(attachmentKindLabel("archive.bin", "application/octet-stream")).toBe("File");
  });
});
